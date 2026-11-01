property pythonPath : @@PYTHON@@
property launcherPath : @@LAUNCHER@@
property monitorEnabled : false

on openNext()
    try
        do shell script (quoted form of pythonPath & " " & quoted form of launcherPath & " start")
        set monitorEnabled to true
    on error errorMessage
        set monitorEnabled to false
        display dialog errorMessage with title "Next 无法启动" buttons {"好"} default button "好" with icon caution
    end try
end openNext

on run
    my openNext()
end run

on reopen
    my openNext()
end reopen

on idle
    if monitorEnabled then
        try
            do shell script (quoted form of pythonPath & " " & quoted form of launcherPath & " ensure")
        on error errorMessage
            -- Report once instead of repeatedly spawning or showing dialogs.
            set monitorEnabled to false
            display dialog errorMessage with title "Next 自动恢复失败" buttons {"好"} default button "好" with icon caution
        end try
    end if
    return 5
end idle

on quit
    set monitorEnabled to false
    try
        do shell script (quoted form of pythonPath & " " & quoted form of launcherPath & " stop")
    on error errorMessage
        display dialog errorMessage with title "Next 关闭提示" buttons {"好"} default button "好" with icon caution
    end try
    continue quit
end quit
