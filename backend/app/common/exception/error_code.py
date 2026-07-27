from enum import Enum


class ErrorCode(str, Enum):
    # Common
    INTERNAL_ERROR = "INTERNAL_ERROR"

    # Conversation
    CONVERSATION_NOT_FOUND = "CONVERSATION_NOT_FOUND"

    # Message
    MESSAGE_NOT_FOUND = "MESSAGE_NOT_FOUND"

    # Authentication
    UNAUTHORIZED = "UNAUTHORIZED"

    FORBIDDEN = "FORBIDDEN"

    # Validation
    VALIDATION_ERROR = "VALIDATION_ERROR"
