# VHS tape for Aldwin 0.0.2 terminal demo (docs/assets/demo.gif)
# Run with: vhs docs/assets/demo.tape

Output docs/assets/demo.gif

Set Shell "zsh"
Set FontSize 15
Set Width 960
Set Height 540
Set Padding 20
Set TypingSpeed 40ms
Set PlaybackSpeed 1.0

Type "uv tool install aldwin"
Enter
Sleep 1.5s

Type "aldwin"
Enter
Sleep 1.5s

# 1. Onboarding: paste OpenAI key (hidden), skip Tavily, accept default model
Type "sk-proj-demo1234567890abcd"
Enter
Sleep 1.2s
Enter
Sleep 0.8s
Enter
Sleep 1.5s

# 2. Ask Aldwin to read and summarise a spreadsheet and a deck (no approval
# needed; read_document is Auto-tier)
Type "Summarise sheet 2 of Q3.xlsx and the deck.pptx"
Enter
Sleep 12s

# 3. Ask it to find and fix a TODO, which edits a file (folded diff approval)
Type "Find every TODO in this folder and fix the first one"
Enter
Sleep 12s

# 4. Approve the edit diff with `y`
Type "y"
Sleep 4s

# 5. Exit cleanly
Type "/exit"
Enter
Sleep 1s
