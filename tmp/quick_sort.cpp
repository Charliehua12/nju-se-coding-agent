#include <iostream>
#include <vector>

// 分区函数（Lomuto 分区）：以最后一个元素为基准
// 将小于等于基准的元素移到左侧，返回基准的最终位置
int partition(std::vector<int>& arr, int low, int high) {
    int pivot = arr[high];   // 选择基准
    int i = low - 1;         // i 指向最后一个小于等于基准的元素

    for (int j = low; j < high; ++j) {
        if (arr[j] <= pivot) {
            ++i;
            std::swap(arr[i], arr[j]);
        }
    }
    // 把基准放到正确位置
    std::swap(arr[i + 1], arr[high]);
    return i + 1;
}

// 递归快排
void quickSort(std::vector<int>& arr, int low, int high) {
    if (low >= high) {
        return;  // 递归终止条件
    }
    int p = partition(arr, low, high);   // 基准位置
    quickSort(arr, low, p - 1);          // 排序左半部分
    quickSort(arr, p + 1, high);         // 排序右半部分
}

// 打印数组
void printArray(const std::vector<int>& arr) {
    for (size_t i = 0; i < arr.size(); ++i) {
        std::cout << arr[i] << (i + 1 < arr.size() ? " " : "");
    }
    std::cout << std::endl;
}

// 对 vector 做一次完整排序（处理空数组等情况）
void sortAll(std::vector<int>& arr) {
    std::cout << "排序前: ";
    printArray(arr);
    quickSort(arr, 0, static_cast<int>(arr.size()) - 1);
    std::cout << "排序后: ";
    printArray(arr);
    std::cout << std::endl;
}

int main() {
    // 常规测试（含重复元素）
    std::vector<int> arr1 = {5, 2, 9, 1, 5, 6};
    sortAll(arr1);

    // 空数组
    std::vector<int> arr2;
    sortAll(arr2);

    // 单元素
    std::vector<int> arr3 = {42};
    sortAll(arr3);

    // 已有序数组
    std::vector<int> arr4 = {1, 2, 3, 4, 5};
    sortAll(arr4);

    // 逆序数组
    std::vector<int> arr5 = {9, 7, 5, 3, 1};
    sortAll(arr5);

    return 0;
}
