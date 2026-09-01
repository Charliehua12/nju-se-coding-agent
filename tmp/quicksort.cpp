#include <iostream>
#include <vector>
using namespace std;

// 分区函数：以最后一个元素为基准，将数组分为小于基准和大于基准两部分
int partition(vector<int>& arr, int low, int high) {
    int pivot = arr[high];  // 选择最后一个元素作为基准
    int i = low - 1;        // i 指向小于基准部分的最后一个元素

    for (int j = low; j < high; j++) {
        if (arr[j] < pivot) {
            i++;
            swap(arr[i], arr[j]);
        }
    }
    // 将基准放到正确位置
    swap(arr[i + 1], arr[high]);
    return i + 1;
}

// 快速排序递归函数
void quickSort(vector<int>& arr, int low, int high) {
    if (low < high) {
        int pi = partition(arr, low, high);  // 基准的最终位置
        quickSort(arr, low, pi - 1);         // 排序左半部分
        quickSort(arr, pi + 1, high);        // 排序右半部分
    }
}

// 打印数组
void printArray(const vector<int>& arr) {
    for (int num : arr) {
        cout << num << " ";
    }
    cout << endl;
}

int main() {
    vector<int> arr = {10, 7, 8, 9, 1, 5};

    cout << "排序前: ";
    printArray(arr);

    quickSort(arr, 0, arr.size() - 1);

    cout << "排序后: ";
    printArray(arr);

    return 0;
}
