# README demo — rendered with VHS (https://github.com/charmbracelet/vhs).
# Re-render with `make demo`; demo/setup.sh rebuilds the sandbox first, so
# every frame is real CLI output against sandbox-only paths.

Output assets/claude-hop.gif

Set Theme "Catppuccin Macchiato"
Set FontSize 16
Set Width 1100
Set Height 620
Set Padding 24
Set TypingSpeed 60ms

Hide
Type "bash demo/setup.sh >/dev/null 2>&1 && export HOME=/tmp/hop-demo/home/demo XDG_CONFIG_HOME=/tmp/hop-demo/xdg PATH=/tmp/hop-demo/bin:$PATH && clear"
Enter
Sleep 2s
Show

# a) the banner (init greets, sees the existing setup, we decline the add)
Type "claude-hop init"
Enter
Sleep 3s
Type "n"
Enter
Sleep 1.5s

# b) both remotes at a glance
Type "claude-hop remotes"
Enter
Sleep 3.5s

# c) hero shot: the path remap
Type "claude-hop status mac"
Enter
Sleep 5s

# d) a real sync
Type "claude-hop push mac"
Enter
Sleep 4.5s

# e) the payoff
Type "# on the mac:  claude --resume ✨"
Sleep 3.5s
