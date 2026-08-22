from enum import Enum


class DocumentParseErrorCode(str, Enum):
    """Worker 可持久化、前端可稳定识别的文档解析错误码。"""

    UNSUPPORTED_DOCUMENT_TYPE = "UNSUPPORTED_DOCUMENT_TYPE"
    INVALID_TEXT_ENCODING = "INVALID_TEXT_ENCODING"
    NO_EXTRACTABLE_TEXT = "NO_EXTRACTABLE_TEXT"
    PDF_ENCRYPTED = "PDF_ENCRYPTED"
    PDF_INVALID = "PDF_INVALID"
    DOCX_INVALID = "DOCX_INVALID"
    XLSX_INVALID = "XLSX_INVALID"
    XLSX_LIMIT_EXCEEDED = "XLSX_LIMIT_EXCEEDED"
    OFFICE_ARCHIVE_UNSAFE = "OFFICE_ARCHIVE_UNSAFE"


class DocumentParseError(Exception):
    """封装安全错误码与中文提示，不直接暴露第三方解析器细节。"""

    def __init__(self, code: DocumentParseErrorCode, message: str) -> None:
        """保存稳定错误码与可安全展示的错误信息。"""
        self.code = code
        self.message = message
        super().__init__(message)


class UnsupportedDocumentTypeError(DocumentParseError):
    """表示 Parser 注册表没有当前文件类型的适配器。"""

    def __init__(self) -> None:
        """构造第一版支持范围之外的文档类型错误。"""
        super().__init__(
            code=DocumentParseErrorCode.UNSUPPORTED_DOCUMENT_TYPE,
            message="暂不支持该文档类型",
        )
