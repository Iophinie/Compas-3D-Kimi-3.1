Dim fso, shell, root, exe
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
exe = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(exe) Then
    MsgBox "Run install.ps1 first.", 16, "Kimi for KOMPAS"
    WScript.Quit 1
End If
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = root
shell.Run """" & exe & """ -m app", 0, False
