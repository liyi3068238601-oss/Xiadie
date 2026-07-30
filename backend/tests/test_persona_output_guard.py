from app import persona_output_guard as guard


def test_natural_dialogue_sanitizer_removes_only_staged_action_spans():
    value = "（微微一怔，声音放轻）我在听。HTTP（超文本传输协议）仍可正常说明。"
    assert guard.sanitize_natural_dialogue(value) == "我在听。HTTP（超文本传输协议）仍可正常说明。"
    assert guard.contains_action_narration(value)


def test_explicit_roleplay_is_narrow_and_negation_does_not_grant_permission():
    assert guard.explicit_narration_requested("我们来玩角色扮演吧，用括号写动作。")
    assert guard.explicit_narration_requested("请帮我写一个带旁白的剧本。")
    assert not guard.explicit_narration_requested("喵呜，吓你一下。")
    assert not guard.explicit_narration_requested("这不是角色扮演，不要写动作旁白。")


def test_stream_guard_holds_cross_chunk_action_and_preserves_normal_parentheses():
    stream = guard.NaturalDialogueStreamGuard(enabled=True)
    chunks = ["（微微", "一怔，声音放轻）我", "在听。HTTP（超文本", "传输协议）可用。"]
    displayed = "".join(stream.push(chunk) for chunk in chunks) + stream.finish()
    assert displayed == "我在听。HTTP（超文本传输协议）可用。"


def test_casual_grounding_guard_removes_invented_ambience_and_audit_jargon():
    value = (
        "今天天气不错，阳光透过书页间洒下来。"
        "现有资料不足以确认：你呢，今天有什么特别想聊的事吗？"
    )
    assert guard.sanitize_natural_dialogue(
        value, suppress_ungrounded_ambience=True,
    ) == "你呢，今天有什么特别想聊的事吗？"

    stream = guard.NaturalDialogueStreamGuard(
        enabled=True, suppress_ungrounded_ambience=True,
    )
    assert stream.push("窗外有风，树叶") == ""
    assert stream.push("小声说话。你想聊什么？") == ""
    assert stream.finish() == "你想聊什么？"


def test_stream_guard_does_not_hold_markdown_bullet_without_closing_star():
    stream = guard.NaturalDialogueStreamGuard(enabled=True)
    assert stream.push("* 第一项\n") == "* 第一项\n"
    assert stream.finish() == ""


def test_disabled_stream_guard_is_transparent_for_roleplay():
    stream = guard.NaturalDialogueStreamGuard(enabled=False)
    value = "（轻轻点头）我明白了。"
    assert stream.push(value) + stream.finish() == value
