from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_skill_owns_the_end_to_end_learner_workflow():
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

    assert "one prompt, one browser confirmation, one live URL" in skill
    assert "Do not turn those internal actions into a checklist for the learner" in skill
    assert "Do not ask the learner to write a manifest" in skill
    assert "perform the verification yourself" in skill
    assert "Treat steps 1–6 as internal Skill execution" in skill


def test_default_prompt_preserves_the_single_action_contract():
    prompt = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "其余步骤由你完成" in prompt
    assert "公网网址、部署编号和线上验收结果" in prompt
