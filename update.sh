echo "update playV"
cp ./playV.desktop /home/verilog/.local/share/applications/
update-desktop-database ~/.local/share/applications
desktop-file-validate ~/.local/share/applications/playV.desktop
killall -SIGUSR1 gnome-shell
