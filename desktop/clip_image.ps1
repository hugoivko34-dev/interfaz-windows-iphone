# Moves images between a file and the Windows clipboard (used by clipboard.py).
#   set <path>: decode a PNG/JPEG/GIF/BMP file and put it on the clipboard
#   get <path>: save the clipboard image as PNG; exit 1 if there is none
param([string]$Mode, [string]$Path)
Add-Type -AssemblyName System.Windows.Forms, System.Drawing

if ($Mode -eq "set") {
    # Load from memory so the file isn't left locked.
    $stream = New-Object System.IO.MemoryStream (, [System.IO.File]::ReadAllBytes($Path))
    $image = [System.Drawing.Image]::FromStream($stream)
    [System.Windows.Forms.Clipboard]::SetImage($image)
    exit 0
}
if ($Mode -eq "get") {
    $image = [System.Windows.Forms.Clipboard]::GetImage()
    if ($null -eq $image) { exit 1 }
    $image.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    exit 0
}
exit 2
