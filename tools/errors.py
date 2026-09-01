"""工具层自定义异常。单独成模块以避免 tools 包内部的循环导入。"""


class RequestDenied(Exception):
    """工具调用被用户拒绝（审查模式下）。"""
