// Wiring diagnostic for an SPI flash on GP1..GP4 (any order). Read-only: sends only the
// JEDEC "read ID" (0x9F) and "read status" (0x05) commands.
#include <stdio.h>
#include "pico/stdlib.h"
static const int P[4] = {1, 2, 3, 4};
static void all_in(void) { for (int i = 0; i < 4; i++) { gpio_init(P[i]); gpio_set_dir(P[i], GPIO_IN); gpio_disable_pulls(P[i]); } }
static void tick(void) { busy_wait_us(20); }
static void xfer(int cs, int sck, int mosi, int miso, uint8_t cmd, uint8_t *out, int n) {
    all_in();
    gpio_pull_up(miso);
    for (int i = 0; i < 4; i++) gpio_set_drive_strength(P[i], GPIO_DRIVE_STRENGTH_2MA);
    gpio_put(cs, 1); gpio_set_dir(cs, GPIO_OUT);
    gpio_put(sck, 0); gpio_set_dir(sck, GPIO_OUT);
    gpio_put(mosi, 0); gpio_set_dir(mosi, GPIO_OUT);
    busy_wait_us(200);
    gpio_put(cs, 0); tick();
    for (int b = 7; b >= 0; b--) { gpio_put(mosi, (cmd >> b) & 1); tick(); gpio_put(sck, 1); tick(); gpio_put(sck, 0); }
    for (int k = 0; k < n; k++) { uint8_t v = 0;
        for (int b = 7; b >= 0; b--) { tick(); gpio_put(sck, 1); v |= gpio_get(miso) << b; tick(); gpio_put(sck, 0); }
        out[k] = v; }
    gpio_put(cs, 1); tick();
    all_in();
}
int main(void) {
    stdio_init_all();
    while (true) {
        int c = getchar_timeout_us(100000);
        if (c == 'a') {
            // Who is talking on the lines? Sample GP0..GP7 as fast as possible, pins as
            // plain inputs, and count level changes and time spent high.
            for (int g = 0; g <= 7; g++) { gpio_init(g); gpio_set_dir(g, GPIO_IN); gpio_disable_pulls(g); }
            static uint32_t trans[8], high[8]; const int N = 2000000;
            for (int r = 0; r < 3; r++) {
                for (int g = 0; g < 8; g++) trans[g] = high[g] = 0;
                uint32_t prev = gpio_get_all(); absolute_time_t t0 = get_absolute_time();
                for (int i = 0; i < N; i++) { uint32_t v = gpio_get_all(); uint32_t d = v ^ prev; prev = v;
                    for (int g = 1; g <= 5; g++) { trans[g] += (d >> g) & 1; high[g] += (v >> g) & 1; } }
                int64_t us = absolute_time_diff_us(t0, get_absolute_time());
                printf("run %d: %d samples in %lld us\n", r, N, us);
                for (int g = 1; g <= 5; g++) printf("  GP%d changes=%lu high=%lu%%\n", g, (unsigned long)trans[g], (unsigned long)(high[g] / (N / 100)));
            }
            printf("END\n");
            continue;
        }
        if (c == 'p') {
            // Is the power wire on GP5 instead of 3V3? Look at GP0..GP7 at rest, then try
            // feeding the chip from GP5 (a pin can give about 12 mA) and ask for its ID.
            printf("BEGIN\n");
            for (int g = 0; g <= 7; g++) {
                gpio_init(g); gpio_set_dir(g, GPIO_IN);
                gpio_pull_down(g); sleep_ms(5); int lo = gpio_get(g);
                gpio_pull_up(g); sleep_ms(5); int hi = gpio_get(g);
                gpio_disable_pulls(g);
                printf("GP%d pulldown=%d pullup=%d -> %s\n", g, lo, hi,
                       lo && hi ? "held HIGH" : !lo && !hi ? "held LOW" : "floating/weak");
            }
            gpio_init(5); gpio_set_drive_strength(5, GPIO_DRIVE_STRENGTH_12MA); gpio_put(5, 1); gpio_set_dir(5, GPIO_OUT);
            for (int t = 0; t < 6; t++) { sleep_ms(100); printf("GP5 driven high, pad reads %d after %d ms\n", gpio_get(5), (t + 1) * 100); }
            for (int i = 0; i < 4; i++) { all_in(); gpio_pull_down(P[i]); sleep_ms(5); printf("powered: GP%d with pulldown reads %d\n", P[i], gpio_get(P[i])); }
            for (int k = 0; k < 3; k++) { uint8_t id[3]; xfer(1, 2, 3, 4, 0x9F, id, 3); printf("ID try %d: %02x %02x %02x\n", k, id[0], id[1], id[2]); }
            gpio_set_dir(5, GPIO_IN);
            printf("END\n");
            continue;
        }
        if (c != 'd') continue;
        printf("BEGIN\n");
        for (int i = 0; i < 4; i++) {
            all_in(); gpio_pull_down(P[i]); sleep_ms(5); int lo = gpio_get(P[i]);
            gpio_pull_up(P[i]); sleep_ms(5); int hi = gpio_get(P[i]);
            printf("GP%d pulldown=%d pullup=%d -> %s\n", P[i], lo, hi,
                   lo && hi ? "held HIGH" : !lo && !hi ? "held LOW" : "floating/weak");
        }
        for (int a = 0; a < 4; a++) for (int b = 0; b < 4; b++) for (int m = 0; m < 4; m++) for (int s = 0; s < 4; s++) {
            if (a == b || a == m || a == s || b == m || b == s || m == s) continue;
            uint8_t id[3], st[1];
            xfer(P[a], P[b], P[m], P[s], 0x9F, id, 3);
            xfer(P[a], P[b], P[m], P[s], 0x05, st, 1);
            printf("CS=GP%d SCK=GP%d MOSI=GP%d MISO=GP%d id=%02x %02x %02x status=%02x\n", P[a], P[b], P[m], P[s], id[0], id[1], id[2], st[0]);
        }
        printf("END\n");
    }
}
