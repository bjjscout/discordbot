"""
Job Queue Management using Redis Queue (RQ)

Provides asynchronous job processing with:
- Persistent job queue (survives bot restarts)
- Job status tracking
- Automatic retries
- Job timeouts

Usage:
    from utils.job_queue import job_queue, get_job_status
    
    # Enqueue a job
    job = job_queue.enqueue('app4.process_video', url, format, transcribe)
    job_id = job.id
    
    # Check status
    status = get_job_status(job_id)
    if status.is_finished:
        result = status.result
"""

import os
import redis
import logging
from typing import Any, Optional, Dict, List, Callable
from dataclasses import dataclass
from enum import Enum
from functools import wraps

# Try to import RQ, but make it optional
try:
    from rq import Queue, Worker
    from rq.job import Job, JobStatus
    RQ_AVAILABLE = True
except ImportError as e:
    RQ_AVAILABLE = False
    Queue = Worker = None
    Job = JobStatus = None
    import logging
    logging.getLogger(__name__).warning(f"RQ import failed: {e}")

logger = logging.getLogger(__name__)


class JobState(Enum):
    """Job execution states"""
    QUEUED = "queued"
    STARTED = "started"
    FINISHED = "finished"
    FAILED = "failed"
    DEFERRED = "deferred"


@dataclass
class JobStatusResult:
    """Job status information"""
    id: str
    state: JobState
    result: Optional[Any] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    ttl: Optional[int] = None
    
    @property
    def is_finished(self) -> bool:
        return self.state == JobState.FINISHED
    
    @property
    def is_failed(self) -> bool:
        return self.state == JobState.FAILED
    
    @property
    def is_running(self) -> bool:
        return self.state == JobState.STARTED
    
    @property
    def is_queued(self) -> bool:
        return self.state == JobState.QUEUED


class JobQueueManager:
    """
    Manages background job processing using Redis Queue.
    
    Features:
    - Asynchronous job execution
    - Job persistence in Redis
    - Automatic retries on failure
    - Job timeout handling
    - Status tracking
    """
    
    def __init__(
        self,
        redis_url: str = None,
        redis_host: str = "localhost",
        redis_port: int = 6379,
        redis_password: str = None,
        redis_db: int = 0,
        default_timeout: int = 3600,
        default_retry: int = 3
    ):
        """
        Initialize job queue manager.
        
        Args:
            redis_url: Full Redis URL (takes precedence over host/port)
            redis_host: Redis host
            redis_port: Redis port
            redis_password: Redis password (optional)
            redis_db: Redis database number
            default_timeout: Default job timeout in seconds
            default_retry: Default number of retries on failure
        """
        if not RQ_AVAILABLE:
            raise ImportError(
                "RQ is not installed. Install with: pip install rq"
            )
        
        # Build Redis URL if not provided
        if redis_url is None:
            if redis_password:
                redis_url = f"redis://:{redis_password}@{redis_host}:{redis_port}/{redis_db}"
            else:
                redis_url = f"redis://{redis_host}:{redis_port}/{redis_db}"
        
        self.redis_url = redis_url
        self.default_timeout = default_timeout
        self.default_retry = default_retry
        
        # Initialize Redis connection
        self._redis = redis.from_url(redis_url)
        
        # Initialize queues
        self._queue = Queue("vidmaker3", connection=self._redis)
        
        # Health check
        try:
            self._redis.ping()
            logger.info(f"Connected to Redis at {redis_url}")
        except redis.ConnectionError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
        
    @property
    def queue(self) -> Queue:
        """Get the main job queue"""
        return self._queue
    
    def enqueue(
        self,
        func: Callable,
        *args,
        job_id: str = None,
        timeout: int = None,
        result_ttl: int = None,
        retry: int = None,
        retry_interval: int = 60,
        **kwargs
    ) -> Job:
        """
        Enqueue a function for background execution.
        
        Args:
            func: Function to execute (must be importable)
            *args: Positional arguments for the function
            job_id: Optional custom job ID
            timeout: Job timeout in seconds (default: from config)
            result_ttl: How long to keep result (default: 24 hours)
            retry: Number of retries on failure (default: from config)
            retry_interval: Seconds between retries
            **kwargs: Keyword arguments for the function
            
        Returns:
            RQ Job instance
        """
        # Build job kwargs
        job_kwargs = {
            "timeout": timeout or self.default_timeout,
            "result_ttl": result_ttl or 86400,  # 24 hours
            "job_id": job_id,
        }
        
        # Set up retry if requested
        if retry or self.default_retry:
            from rq.retry import Retry
            job_kwargs["retry"] = Retry(
                max=self.default_retry if retry is None else retry,
                interval=retry_interval
            )
        
        # Enqueue the job
        try:
            job = self._queue.enqueue(func, *args, **kwargs, **job_kwargs)
            logger.info(
                f"Enqueued job {job.id}: {func.__module__}.{func.__name__}",
                job_id=job.id,
                function=f"{func.__module__}.{func.__name__}"
            )
            return job
        except Exception as e:
            logger.error(f"Failed to enqueue job: {e}")
            raise
    
    def enqueue_unique(
        self,
        func: Callable,
        *args,
        job_id: str = None,
        timeout: int = None,
        result_ttl: int = None,
        **kwargs
    ) -> Job:
        """
        Enqueue a job, but only if a job with the same ID doesn't exist.
        
        Useful for preventing duplicate processing.
        """
        from rq.exceptions import DuplicateJobError
        
        job_kwargs = {
            "timeout": timeout or self.default_timeout,
            "result_ttl": result_ttl or 86400,
            "job_id": job_id,
            "meta": kwargs.pop("meta", {}),
        }
        
        try:
            # Use unique argument to ensure idempotency
            import hashlib
            import json
            
            # Create a hash of the function and arguments
            key_data = {
                "func": f"{func.__module__}.{func.__name__}",
                "args": str(args),
                "kwargs": str(sorted(kwargs.items()))
            }
            key_hash = hashlib.sha256(json.dumps(key_data).encode()).hexdigest()[:16]
            
            unique_id = f"{func.__name__}_{key_hash}"
            
            # Check if job already exists
            existing_job = self._queue.job_class.fetch(unique_id, self._redis)
            if existing_job and existing_job.status:
                logger.info(f"Job {unique_id} already exists, returning existing")
                return existing_job
            
            job = self._queue.enqueue_call(func, args=args, kwargs=kwargs, **job_kwargs)
            return job
            
        except Exception as e:
            logger.error(f"Failed to enqueue unique job: {e}")
            raise
    
    def get_job_status(self, job_id: str) -> Optional[JobStatusResult]:
        """
        Get the status of a job.
        
        Args:
            job_id: Job ID to check
            
        Returns:
            JobStatusResult if job exists, None otherwise
        """
        if not RQ_AVAILABLE or Job is None:
            return None
            
        try:
            job = Job.fetch(job_id, connection=self._redis)
            
            if job is None:
                return None
            
            # Map RQ status to our status
            state = JobState.QUEUED
            if job.status == JobStatus.FINISHED:
                state = JobState.FINISHED
            elif job.status == JobStatus.FAILED:
                state = JobState.FAILED
            elif job.status == JobStatus.STARTED:
                state = JobState.STARTED
            elif job.status == JobStatus.DEFERRED:
                state = JobState.DEFERRED
            
            return JobStatusResult(
                id=job.id,
                state=state,
                result=job.result if state == JobState.FINISHED else None,
                error=job.exc_info if state == JobState.FAILED else None,
                created_at=job.created_at.isoformat() if job.created_at else None,
                started_at=job.started_at.isoformat() if job.started_at else None,
                ended_at=job.ended_at.isoformat() if job.ended_at else None,
                ttl=job.ttl
            )
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            return None
    
    def cancel_job(self, job_id: str) -> bool:
        """Cancel a queued job"""
        try:
            job = Job.fetch(job_id, connection=self._redis)
            if job:
                job.cancel()
                logger.info(f"Cancelled job {job_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {e}")
        return False
    
    def get_queue_stats(self) -> Dict[str, int]:
        """Get queue statistics"""
        return {
            "queued": len(self._queue),
            "workers": len(Worker.all(connection=self._redis)),
        }
    
    def clear_failed_jobs(self) -> int:
        """Clear all failed jobs from the queue"""
        from rq.command import send_kill_horse_command
        failed_job_ids = self._queue.failed_job_registry
        count = len(failed_job_ids)
        # Actually remove them
        for job_id in failed_job_ids:
            try:
                job = Job.fetch(job_id, connection=self._redis)
                if job:
                    job.delete()
            except:
                pass
        return count


# Global job queue instance
_job_queue: Optional[JobQueueManager] = None


def get_job_queue() -> JobQueueManager:
    """Get or create the global job queue instance"""
    global _job_queue
    if _job_queue is None:
        # Try to get settings
        try:
            from utils.config import get_settings
            settings = get_settings()
            redis_settings = settings.redis
            _job_queue = JobQueueManager(
                redis_url=redis_settings.url,
                default_timeout=settings.processing.job_timeout
            )
        except Exception as e:
            # Fallback to defaults
            logger.warning(f"Could not load settings, using defaults: {e}")
            _job_queue = JobQueueManager()
    return _job_queue


# Convenience functions
def job_queue(*args, **kwargs):
    """Decorator to automatically enqueue a function"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            queue = get_job_queue()
            return queue.enqueue(func, *args, **kwargs)
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            queue = get_job_queue()
            return queue.enqueue(func, *args, **kwargs)
        
        # Return appropriate wrapper
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator


def get_job_status(job_id: str) -> Optional[JobStatusResult]:
    """Get status of a job by ID"""
    queue = get_job_queue()
    return queue.get_job_status(job_id)
