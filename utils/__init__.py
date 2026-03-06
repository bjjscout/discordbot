# Utils package
# Contains helper modules for the Vidmaker3 bot

from .config import Settings, get_settings
from .circuit_breaker import CircuitBreaker, circuit_breaker, CircuitOpenError, get_circuit
from .logging_config import setup_logging, get_logger

# JobQueue requires Redis - optional import for Windows compatibility
try:
    from .job_queue import JobQueueManager, get_job_queue
    JobQueueAvailable = True
except (ImportError, ValueError) as e:
    # RQ doesn't work on Windows without Redis server
    JobQueueManager = None
    get_job_queue = None
    JobQueueAvailable = False

from .service_clients import TranscriptionClient, FaceDetectionClient, VideoProcessingClient
from .whisperx_client import WhisperXClient, get_whisperx_client, close_whisperx_client

__all__ = [
    'Settings',
    'get_settings',
    'CircuitBreaker', 
    'circuit_breaker',
    'CircuitOpenError',
    'get_circuit',
    'setup_logging',
    'get_logger',
    'JobQueueManager',
    'get_job_queue',
    'JobQueueAvailable',
    'TranscriptionClient',
    'FaceDetectionClient',
    'VideoProcessingClient',
    'WhisperXClient',
    'get_whisperx_client',
    'close_whisperx_client',
]
