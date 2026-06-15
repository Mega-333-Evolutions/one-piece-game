import sys
filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

# Make basic changes in message service just like screens
content = content.replace('update: Update', 'event')
content = content.replace('context: ContextTypes.DEFAULT_TYPE', 'client')
content = content.replace('context: CallbackContext', 'client')
content = content.replace('update.effective_chat.id', 'event.chat_id')
content = content.replace('update.effective_user.id', 'event.sender_id')
content = content.replace('update.message.text', 'event.text')
content = content.replace('update.effective_message.text', 'event.text')
content = content.replace('update.callback_query.data', 'event.data.decode("utf-8") if event.data else None')

with open(filepath, 'w') as f:
    f.write(content)
