import sys
filepath = sys.argv[1]
with open(filepath, 'r') as f:
    content = f.read()

content = content.replace('from distutils.util import strtobool', '''def strtobool(val):
    val = val.lower()
    if val in ('y', 'yes', 't', 'true', 'on', '1'):
        return 1
    elif val in ('n', 'no', 'f', 'false', 'off', '0'):
        return 0
    else:
        raise ValueError("invalid truth value %r" % (val,))''')

with open(filepath, 'w') as f:
    f.write(content)
