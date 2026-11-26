/* SPDX-License-Identifier: MIT
 *
 * TerminateThread() a thread while it closes handles, 20,000 times, and check
 * that the killer's own handle calls still return. Without the fix in
 * wine-uninterrupted-section-abort.patch the victim dies holding ntdll's
 * fd_cache_mutex and the next CloseHandle() or wait hangs forever.
 * Writes its result to terminate-thread-lock-test.txt in the current directory.
 */
#include <windows.h>

static volatile LONG phase, step;
static HANDLE out;

static void say(const char *s) {  DWORD n; DWORD l = 0; while (s[l]) l++; WriteFile(out, s, l, &n, NULL);  FlushFileBuffers(out); }
static void num(const char *p, unsigned v)
{
    char b[64]; char t[16]; int i = 0, j = 0;
    while (p[j]) b[i++] = p[j++];
    j = 0; do t[j++] = '0' + v % 10; while (v /= 10);
    while (j) b[i++] = t[--j];
    b[i++] = '\n'; b[i] = 0; say(b);
}

static DWORD WINAPI victim(void *arg)
{
    for (;;) CloseHandle(CreateEventW(NULL, TRUE, FALSE, NULL));
}

static DWORD WINAPI watchdog(void *arg)
{
    for (;;)
    {
        LONG before = phase;
        Sleep(5000);
        if (phase == before)
        {
            num("DEADLOCK at iteration ", (unsigned)before);
            num("main thread stuck in step ", (unsigned)step);
            Sleep(INFINITE);
        }
    }
}

void mainCRTStartup(void)
{
    unsigned i, iters = 20000;
    out = CreateFileA("terminate-thread-lock-test.txt", GENERIC_WRITE, FILE_SHARE_READ, NULL, CREATE_ALWAYS, 0, NULL);
    CloseHandle(CreateThread(NULL, 0, watchdog, NULL, 0, NULL));
    for (i = 0; i < iters; i++)
    {
        HANDLE t = CreateThread(NULL, 0, victim, NULL, 0, NULL);
        Sleep(i % 3);                          /* let it run a few hundred closes */
        step = 1; TerminateThread(t, 0);
        step = 2; WaitForSingleObject(t, INFINITE);
        step = 3; CloseHandle(t);              /* NtClose: needs fd_cache_mutex */
        step = 4;
        CloseHandle(CreateEventW(NULL, TRUE, FALSE, NULL));
        InterlockedIncrement(&phase);
        if (!(i % 2000)) num("iteration ", i);
    }
    num("PASS: no deadlock after iterations ", iters);
    ExitProcess(0);
}
