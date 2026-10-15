' Abre o painel sem nenhuma janela (nem a do abrir-painel.cmd). Use este atalho no dia a dia.
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & folder & "\abrir-painel.cmd""", 0, False
