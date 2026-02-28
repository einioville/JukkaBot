# Description
- Target is to make a discord bot that is capable of playing music from youtube
- Language used is Python

# Features
- Use slash "/" -commands to run commands
- All features are per server and should be managed so that they can be identified by the server

## Commands
- Running every command should follow this procedure before being ran:
1. If bot is not playing or joined a channel proceed to the command
2. If bot is at a channel and the user is at the same channel proceed to the command

### play
- Writing "/play" to the chat opens a query where user is asked for track name
  - When user types to the box there is a 2 second threshold, after triggered the bot searches youtube with the text currently written by the user and suggests songs to be played
- If there is no tracks playing the bot tries to join the channel the user is currently in
  - If this fails, it outputs a message to the user telling the issue
- If there is tracks playing and in the queue the bot checks if the user is in the same channel as the bot
  - If yes, the bot adds the requested song to the queue
  - If not, the bot tells the user that it needs to be in the same voice channel as the bot
- If i missed something remind me. The play command should act the same as in Spotify for example


### Join
- Try to join the channel the user is currently
  - If this fails tell the user
- If bot is currently at any channel don't switch channels
  - Only switch if the bot is alone in a channel
    - Notify user about this
  - Ignore if the user is at same channel as the bot already

### skip
- skips the song if bot is currently playing
- User needs to be on the same channel as the bot

### banuser
- This command can be only executed by me and the admins of the server
- Bans specific user from queueing and skipping songs

### unbanuser
- Similar as the banuser, but does the opposite

## Automation
- If the bot is only user in the channel leave after 5 minutes of inactivity

## Queue system
- Queue system should be per server and it works similar to spotifys and other streaming platform

## currenly playing message
- Remove the last "currently playing" message before sending new one
- Displays the thumbnail, title, author, lenght of the currently played media and the name of the next track
- Add reaction buttons to:
  - Seek last and next track
    - If media has been played more than 5 seconds the last media starts the current media from start
    - When going to previous track, put the current media back to the queue
  - pause
  - shuffle queue
  - loop the current track

# Architecure
- src/ holds the code
- Organize things into different files
  - For example queue system, currently playing generator etc. to different files.
- I will fix the structure later and you can update this