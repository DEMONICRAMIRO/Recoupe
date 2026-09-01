from dataclasses import dataclass


@dataclass(frozen=True)
class MessageTemplate:
    channel: str
    subject: str | None
    body: str


def render(template: MessageTemplate, **kwargs) -> MessageTemplate:
    rendered_body = template.body.format(**kwargs)
    rendered_subject = template.subject.format(**kwargs) if template.subject else None
    return MessageTemplate(
        channel=template.channel,
        subject=rendered_subject,
        body=rendered_body,
    )
