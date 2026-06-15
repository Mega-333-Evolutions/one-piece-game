import sys
import re

filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

content = content.replace('update.message.reply_to_message', 'event.reply_to_msg_id')
content = content.replace('update.effective_message.reply_to_message', 'event.reply_to_msg_id')
content = content.replace('update.effective_chat', 'event.chat')
content = content.replace('update.effective_user', 'event.sender')

with open(filepath, 'w') as f:
    f.write(content)
