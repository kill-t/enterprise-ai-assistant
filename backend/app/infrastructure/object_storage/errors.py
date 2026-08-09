class ObjectStorageReadLimitExceededError(Exception):
    """表示远端对象大于业务记录允许读取的字节数。"""
