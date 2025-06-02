# tmx_server_logic/exceptions.py

class TMXServerError(Exception):
    """Base class for exceptions in this module."""
    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message = message
        self.details = details

class SessionInactiveError(TMXServerError):
    """Raised when an operation requires an active TMX session but none exists."""
    def __init__(self, message: str = "No active TMX file session. Please open a file first.", details: str = ""):
        super().__init__(message, details)

class TMXParsingError(TMXServerError):
    """Raised when there's an error parsing a TMX file."""
    def __init__(self, message: str = "Error parsing TMX file.", details: str = ""):
        super().__init__(message, details)

class InvalidOperationError(TMXServerError):
    """Raised for operations that are invalid under the current state or input."""
    pass # Message will be provided at raise site

class ResourceNotFoundError(TMXServerError):
    """Raised when a required resource (e.g., TU, TUV, file) is not found."""
    pass # Message will be provided at raise site
    
class DatabaseError(TMXServerError):
    """Raised for database related errors not covered by others."""
    def __init__(self, message: str = "A database error occurred.", details: str = ""):
        super().__init__(message, details)
