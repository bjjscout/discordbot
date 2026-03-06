"""
Circuit Breaker Implementation

Provides resilience against cascading failures when calling external services.
When a service fails repeatedly, the circuit "opens" and fast-fails subsequent
requests until the service recovers.

Usage:
    @circuit_breaker(failure_threshold=5, timeout=60)
    async def call_external_service():
        ...
"""

import asyncio
import time
import functools
import logging
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Failing, requests are rejected immediately
    HALF_OPEN = "half_open"  # Testing if service recovered


class CircuitOpenError(Exception):
    """Raised when circuit is open and request is rejected"""
    def __init__(self, service_name: str, retry_after: float):
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(f"Circuit open for {service_name}, retry after {retry_after:.1f}s")


@dataclass
class CircuitBreaker:
    """
    Circuit breaker implementation.
    
    Attributes:
        name: Identifier for this circuit breaker
        failure_threshold: Number of failures before opening circuit
        success_threshold: Number of successes needed to close circuit from half-open
        timeout: Seconds to wait before attempting to close circuit
        half_open_max_calls: Max calls allowed in half-open state
    """
    name: str
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 60.0
    half_open_max_calls: int = 3
    
    # Internal state
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _success_count: int = field(default=0, init=False)
    _last_failure_time: float = field(default=0, init=False)
    _half_open_calls: int = field(default=0, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    
    @property
    def state(self) -> CircuitState:
        return self._state
    
    @property
    def is_closed(self) -> bool:
        return self._state == CircuitState.CLOSED
    
    @property
    def is_open(self) -> bool:
        return self._state == CircuitState.OPEN
    
    @property
    def is_half_open(self) -> bool:
        return self._state == CircuitState.HALF_OPEN
    
    def _try_close(self) -> bool:
        """Check if circuit should close (return to normal operation)"""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                logger.info(f"Circuit {self.name}: Closing after {self._success_count} successes")
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                return True
        return False
    
    def _try_open(self) -> None:
        """Check if circuit should open (start rejecting requests)"""
        if self._failure_count >= self.failure_threshold:
            logger.warning(
                f"Circuit {self.name}: Opening after {self._failure_count} failures"
            )
            self._state = CircuitState.OPEN
            self._last_failure_time = time.time()
    
    def record_success(self) -> None:
        """Record a successful call"""
        self._failure_count = 0
        if self._state == CircuitState.HALF_OPEN:
            self._try_close()
    
    def record_failure(self) -> None:
        """Record a failed call"""
        self._failure_count += 1
        self._success_count = 0
        self._try_open()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Raises:
            CircuitOpenError: If circuit is open
        """
        async with self._lock:
            # Check if circuit just timed out
            if self._state == CircuitState.OPEN:
                if time.time() - self._last_failure_time >= self.timeout:
                    logger.info(f"Circuit {self.name}: Half-open (timeout elapsed)")
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    self._success_count = 0
                else:
                    retry_after = self.timeout - (time.time() - self._last_failure_time)
                    raise CircuitOpenError(self.name, retry_after)
            
            # Check half-open limit
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(self.name, 1.0)
                self._half_open_calls += 1
        
        # Execute the function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            self.record_success()
            return result
        except Exception as e:
            self.record_failure()
            raise


# Global circuit breakers registry
_circuits: dict[str, CircuitBreaker] = {}


def get_circuit(name: str, **kwargs) -> CircuitBreaker:
    """Get or create a named circuit breaker"""
    if name not in _circuits:
        _circuits[name] = CircuitBreaker(name=name, **kwargs)
    return _circuits[name]


def circuit_breaker(
    name: str = None,
    failure_threshold: int = 5,
    timeout: float = 60.0,
    success_threshold: int = 2
):
    """
    Decorator to add circuit breaker protection to a function.
    
    Usage:
        @circuit_breaker(name="transcription", failure_threshold=3)
        async def transcribe_video(url):
            ...
    """
    def decorator(func: Callable) -> Callable:
        circuit_name = name or func.__module__ + "." + func.__name__
        circuit = get_circuit(
            circuit_name,
            failure_threshold=failure_threshold,
            timeout=timeout,
            success_threshold=success_threshold
        )
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            return await circuit.call(func, *args, **kwargs)
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            # For sync functions, we need to handle it differently
            # This is a simplified version
            try:
                return func(*args, **kwargs)
            except Exception as e:
                circuit.record_failure()
                raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
