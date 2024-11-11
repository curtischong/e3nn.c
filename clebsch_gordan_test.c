#include <stdio.h>

#include "clebsch_gordan.h"

int main() {
    build_sparse_clebsch_gordan_cache();
    printf("Testing clebsch_gordan...\n");
    for (int l1 = 0; l1 < 2; l1++) {
        for (int l2 = 0; l2 < 2; l2++) {
            for (int l3 = 0; l3 < 2; l3++) {
                for (int m1 = -l1; m1 <= l1; m1++) {
                    for (int m2 = -l2; m2 <= l2; m2++) {
                        for (int m3 = -l3; m3 <= l3; m3++) {
                            printf("hi\n");
                            // double cg = 2;
                            double cg = clebsch_gordan(l1, l2, l3, m1, m2, m3);
                            printf("l1=%d, l2=%d, l3=%d, m1=%d, m2=%d, m3=%d, cg=%f\n", 
                                   l1, l2, l3, m1, m2, m3, cg);
                        }
                    }
                }
            }
        }
    }
    return 0;
}
