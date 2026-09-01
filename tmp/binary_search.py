"""二分查找程序

在一个有序（升序）数组中查找目标值，返回其下标；若不存在则返回 -1。
"""


def binary_search(arr, target):
    """在有序数组 arr 中查找 target。

    参数:
        arr: 升序排列的有序数组
        target: 要查找的目标值

    返回:
        target 在 arr 中的下标；若不存在则返回 -1
    """
    left = 0
    right = len(arr) - 1

    while left <= right:
        mid = (left + right) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            left = mid + 1
        else:
            right = mid - 1

    return -1


if __name__ == "__main__":
    # 测试用例
    arr = [1, 3, 5, 7, 9, 11, 13]

    # 目标值在数组中间
    assert binary_search(arr, 7) == 3
    # 目标值在数组开头
    assert binary_search(arr, 1) == 0
    # 目标值在数组结尾
    assert binary_search(arr, 13) == 6
    # 目标值不存在于数组中
    assert binary_search(arr, 8) == -1
    # 目标值小于最小元素
    assert binary_search(arr, 0) == -1
    # 目标值大于最大元素
    assert binary_search(arr, 100) == -1

    # 空数组
    assert binary_search([], 1) == -1

    # 单个元素数组
    assert binary_search([5], 5) == 0
    assert binary_search([5], 3) == -1

    print("所有测试用例通过 ✅")
