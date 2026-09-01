def partition(arr, low, high):
    """划分函数：以最后一个元素为基准，返回基准最终位置"""
    pivot = arr[high]  # 选择基准
    i = low - 1        # i 指向小于基准的区域的末尾

    for j in range(low, high):
        if arr[j] < pivot:
            i += 1
            arr[i], arr[j] = arr[j], arr[i]

    # 将基准放到正确位置
    arr[i + 1], arr[high] = arr[high], arr[i + 1]
    return i + 1


def quick_sort(arr, low, high):
    """快速排序（递归实现）"""
    if low < high:
        pi = partition(arr, low, high)  # 划分
        quick_sort(arr, low, pi - 1)    # 排序左半部分
        quick_sort(arr, pi + 1, high)   # 排序右半部分


def run_tests():
    """运行测试样例，用 Python 内置 sorted 作为期望结果进行比对"""
    test_cases = [
        [3, 1, 2],  # 简单乱序数组
        [],          # 空数组
        [5],         # 单元素数组
    ]

    for idx, case in enumerate(test_cases, start=1):
        arr = list(case)                  # 复制一份，避免影响原始数据
        expected = sorted(case)           # 内置 sorted 作为期望结果
        quick_sort(arr, 0, len(arr) - 1)
        assert arr == expected, f"用例 {idx} 失败: 输入 {case}, 期望 {expected}, 实际 {arr}"
        print(f"用例 {idx} 通过")


def main():
    print("开始运行测试...")
    run_tests()
    print("全部测试通过!")

    arr = [10, 7, 8, 9, 1, 5]

    print("排序前:", end=" ")
    for x in arr:
        print(x, end=" ")
    print()

    quick_sort(arr, 0, len(arr) - 1)

    print("排序后:", end=" ")
    for x in arr:
        print(x, end=" ")
    print()


if __name__ == "__main__":
    main()
