#include <stdio.h>

volatile int racp_marker = 37;
const char racp_message[] = "RACP known-source analysis and debugger fixture";

__attribute__((noinline)) int racp_add(int value) {
    return value + racp_marker;
}

int main(void) {
    int result = racp_add(5);
    puts(racp_message);
    return result == 42 ? 0 : 1;
}
