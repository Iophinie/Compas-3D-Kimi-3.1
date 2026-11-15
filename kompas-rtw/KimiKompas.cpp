#include <windows.h>
#include <shellapi.h>
#include <string>

static HMODULE g_module = nullptr;

static void LaunchPanel()
{
    wchar_t modulePath[MAX_PATH] = {0};
    if (!GetModuleFileNameW(g_module, modulePath, MAX_PATH))
        return;

    wchar_t* slash = wcsrchr(modulePath, L'\\');
    if (!slash)
        return;
    *slash = 0;
    std::wstring hostDir(modulePath);
    std::wstring root = hostDir + L"\\..";
    std::wstring vbs = root + L"\\run.vbs";

    if (GetFileAttributesW(vbs.c_str()) != INVALID_FILE_ATTRIBUTES)
    {
        std::wstring arguments = L"\"" + vbs + L"\"";
        ShellExecuteW(nullptr, L"open", L"wscript.exe", arguments.c_str(), root.c_str(), SW_SHOWNORMAL);
        return;
    }

    std::wstring exe = hostDir + L"\\KompasChatPanel.exe";
    ShellExecuteW(nullptr, L"open", exe.c_str(), nullptr, hostDir.c_str(), SW_SHOWNORMAL);
}

extern "C" __declspec(dllexport) unsigned int WINAPI LIBRARYID()
{
    return 100;
}

extern "C" __declspec(dllexport) const char* WINAPI LIBRARYNAME()
{
    return "Kimi";
}

extern "C" __declspec(dllexport) const wchar_t* WINAPI LIBRARYNAMEW()
{
    return L"Kimi для КОМПАС";
}

extern "C" __declspec(dllexport) int WINAPI LibIsOnApplication7()
{
    return 1;
}

extern "C" __declspec(dllexport) void WINAPI LIBRARYENTRY(unsigned int command)
{
    if (command == 1)
        LaunchPanel();
}

BOOL APIENTRY DllMain(HMODULE module, DWORD, LPVOID)
{
    g_module = module;
    return TRUE;
}
