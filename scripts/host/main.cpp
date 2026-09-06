// LiveAIO.exe — process shell: lifecycle, UAC, module load. Not a business owner.
// Owns: elevation, working directory / Qt plugin env, LoadLibrary Core, process exit.
// Does not own: hub protocol, capture, tool rules (Core), widgets (Pages/Tools).
#ifndef NOMINMAX
#  define NOMINMAX
#endif
#include <windows.h>
#include <shellapi.h>

#include <string>
#include <vector>

namespace {

using CoreMainFn = int(__cdecl*)(int argc, char** argv);
using PagesRunFn = int(__cdecl*)(int argc, char** argv);

std::wstring exeDir() {
    wchar_t buf[MAX_PATH]{};
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring path(buf, n ? n : 0);
    const size_t slash = path.find_last_of(L"\\/");
    if (slash == std::wstring::npos) return L".";
    return path.substr(0, slash);
}

std::wstring exePath() {
    wchar_t buf[MAX_PATH]{};
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    return std::wstring(buf, n ? n : 0);
}

std::wstring utf8ToWide(const std::string& s) {
    if (s.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    if (n <= 1) return {};
    std::wstring w(static_cast<size_t>(n - 1), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, w.data(), n);
    return w;
}

void setQtPluginEnv(const std::wstring& dir) {
    SetEnvironmentVariableW(L"QT_PLUGIN_PATH", dir.c_str());
    const std::wstring platforms = dir + L"\\platforms";
    SetEnvironmentVariableW(L"QT_QPA_PLATFORM_PLUGIN_PATH", platforms.c_str());
}

bool isElevated() {
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
    TOKEN_ELEVATION elev{};
    DWORD got = 0;
    const BOOL ok = GetTokenInformation(token, TokenElevation, &elev, sizeof(elev), &got);
    CloseHandle(token);
    return ok && elev.TokenIsElevated != 0;
}

bool wantNoAdmin(const std::vector<std::string>& args) {
    for (size_t i = 1; i < args.size(); ++i) {
        if (args[i] == "--no-admin" || args[i] == "-no-admin") return true;
    }
    return false;
}

enum class ElevateResult { OkContinue, ScheduledChild, Failed };

ElevateResult ensureElevated(const std::vector<std::string>& args) {
    if (wantNoAdmin(args) || isElevated()) {
        SetEnvironmentVariableW(L"LIVEAIO_SHELL_ELEVATED", L"1");
        return ElevateResult::OkContinue;
    }
    std::wstring params;
    for (size_t i = 1; i < args.size(); ++i) {
        if (!params.empty()) params.push_back(L' ');
        params.push_back(L'"');
        params += utf8ToWide(args[i]);
        params.push_back(L'"');
    }
    const std::wstring file = exePath();
    const std::wstring cwd = exeDir();
    SHELLEXECUTEINFOW sei{};
    sei.cbSize = sizeof(sei);
    sei.fMask = SEE_MASK_NOCLOSEPROCESS;
    sei.lpVerb = L"runas";
    sei.lpFile = file.c_str();
    sei.lpParameters = params.empty() ? nullptr : params.c_str();
    sei.lpDirectory = cwd.c_str();
    sei.nShow = SW_SHOWNORMAL;
    if (!ShellExecuteExW(&sei)) return ElevateResult::Failed;
    if (sei.hProcess) CloseHandle(sei.hProcess);
    return ElevateResult::ScheduledChild;
}

std::vector<std::string> narrowArgs(int argc, wchar_t** wargv) {
    std::vector<std::string> out;
    out.reserve(argc > 0 ? static_cast<size_t>(argc) : 1);
    for (int i = 0; i < argc; ++i) {
        if (!wargv[i]) {
            out.emplace_back();
            continue;
        }
        const int need = WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, nullptr, 0, nullptr, nullptr);
        if (need <= 1) {
            out.emplace_back();
            continue;
        }
        std::string s(static_cast<size_t>(need - 1), '\0');
        WideCharToMultiByte(CP_UTF8, 0, wargv[i], -1, s.data(), need, nullptr, nullptr);
        out.push_back(std::move(s));
    }
    if (out.empty()) out.emplace_back("LiveAIO.exe");
    return out;
}

bool wantPagesOnly(const std::vector<std::string>& args) {
    for (size_t i = 1; i < args.size(); ++i) {
        if (args[i] == "pages" || args[i] == "--pages") return true;
    }
    return false;
}

int fail(const wchar_t* msg) {
    MessageBoxW(nullptr, msg, L"LiveAIO", MB_ICONERROR | MB_OK);
    return 1;
}

template <typename Fn>
Fn procAddress(HMODULE mod, const char* name) {
    return reinterpret_cast<Fn>(reinterpret_cast<void*>(GetProcAddress(mod, name)));
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    const std::wstring dir = exeDir();
    SetDllDirectoryW(dir.c_str());
    setQtPluginEnv(dir);
    SetCurrentDirectoryW(dir.c_str());
    // F5 / Supervisor 可能已设 LIVEAIO_ROOT=仓库根；勿用 exe 目录覆盖（custom/ 无 resources）。
    wchar_t existingRoot[4]{};
    if (GetEnvironmentVariableW(L"LIVEAIO_ROOT", existingRoot, 4) == 0) {
        SetEnvironmentVariableW(L"LIVEAIO_ROOT", dir.c_str());
    }
    SetEnvironmentVariableW(L"LIVEAIO_SHELL", L"1");

    int argc = 0;
    wchar_t** wargv = CommandLineToArgvW(GetCommandLineW(), &argc);
    auto args = narrowArgs(argc, wargv);
    if (wargv) LocalFree(wargv);

    switch (ensureElevated(args)) {
    case ElevateResult::ScheduledChild:
        return 0;
    case ElevateResult::Failed:
        return fail(L"需要管理员权限才能运行 LiveAIO");
    case ElevateResult::OkContinue:
        break;
    }

    std::vector<char*> argv;
    argv.reserve(args.size());
    for (auto& s : args) argv.push_back(s.data());
    const int n = static_cast<int>(argv.size());

    if (wantPagesOnly(args)) {
        const std::wstring dllPath = dir + L"\\LiveAIOPages.dll";
        HMODULE mod = LoadLibraryW(dllPath.c_str());
        if (!mod) return fail(L"无法加载 LiveAIOPages.dll");
        auto run = procAddress<PagesRunFn>(mod, "LiveAIO_PagesRun");
        if (!run) return fail(L"LiveAIOPages.dll 缺少 LiveAIO_PagesRun");
        return run(n, argv.data());
    }

    const std::wstring dllPath = dir + L"\\LiveAIOCore.dll";
    HMODULE mod = LoadLibraryW(dllPath.c_str());
    if (!mod) return fail(L"无法加载 LiveAIOCore.dll");
    auto mainFn = procAddress<CoreMainFn>(mod, "LiveAIO_CoreMain");
    if (!mainFn) return fail(L"LiveAIOCore.dll 缺少 LiveAIO_CoreMain");

    // Core 内跑 Supervisor（总线+模块）；壳负责提权/环境/加载与进程身份。
    return mainFn(n, argv.data());
}
