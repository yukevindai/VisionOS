"""Read-only transcript presentation; never routes requests or changes recognition."""
from contextlib import nullcontext


def voice_view(voice):
    with getattr(voice, 'state_lock', nullcontext()):
        items = list(getattr(voice, 'transcript', ()))
    latest = items[-1] if items else {}
    text = str(latest.get('text', ''))[:500]
    command = latest.get('command')
    return dict(
        status=str(getattr(voice, 'status', 'Off'))[:200],
        lastHeard=str(getattr(voice, 'last_heard', ''))[:500],
        inputDevice=str(getattr(voice, 'input_device_name', 'Not selected'))[:200],
        earlier=[str(item.get('text', ''))[:500] for item in items[-3:-1]][::-1],
        transcript=text,
        recognition=('Recognized request' if command == 'request' else
                     'Recognized command' if command else 'Not accepted as a command') if text else '',
        submittedPrompt=str(getattr(voice, 'last_agent_prompt', ''))[:1000],
    )


def transcript_label(voice):
    view = voice_view(voice)
    text = f'You said: "{view["transcript"]}"' if view['transcript'] else 'You said: No speech recognized yet.'
    if view['earlier']:
        text += '\nearlier: ' + '   |   '.join(view['earlier'])
    if view['recognition']:
        text += '\n' + view['recognition']
    if view['submittedPrompt']:
        text += '\nLast sent to agent: ' + view['submittedPrompt']
    return text
