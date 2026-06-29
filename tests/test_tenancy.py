from core.skills_loader import SkillsLoader
from core.tenancy import TenantRegistry


def _write_skill(root, name, description):
    skill_dir = root / name
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
        encoding="utf-8",
    )
    (scripts_dir / "main.py").write_text(
        "def execute(*args, **kwargs):\n    return {'text': 'ok'}\n",
        encoding="utf-8",
    )


def _write_skill_without_execute(root, name, description):
    skill_dir = root / name
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{description}\n",
        encoding="utf-8",
    )
    (scripts_dir / "main.py").write_text(
        "def run(*args, **kwargs):\n    return {'text': 'ok'}\n",
        encoding="utf-8",
    )


def test_tenant_registry_loads_yaml_config_without_building_runtime(tmp_path):
    tenants_root = tmp_path / "tenants"
    tenant_dir = tenants_root / "acme"
    tenant_dir.mkdir(parents=True)
    (tenant_dir / "config.yaml").write_text(
        """
id: acme
name: Acme
feishu:
  app_id: cli_acme
  app_secret: secret
llm:
  provider: gemini
  gemini_api_key: key
limits:
  max_images: 2
  max_output_images: 3
""",
        encoding="utf-8",
    )

    registry = TenantRegistry(tenants_root=tenants_root, default_tenant_id="default")
    configs = {cfg.tenant_id: cfg for cfg in registry.list_configs()}

    assert configs["acme"].feishu.app_id == "cli_acme"
    assert configs["acme"].limits.max_images == 2
    assert configs["acme"].limits.max_output_images == 3
    assert "default" not in configs


def test_tenant_skill_overrides_common_skill(tmp_path):
    common_skills = tmp_path / "common"
    tenant_skills = tmp_path / "tenant"
    _write_skill(common_skills, "general", "common general")
    _write_skill(tenant_skills, "general", "tenant general")

    loader = SkillsLoader(
        workspace=tmp_path,
        tenant_skills_dir=tenant_skills,
        common_skills_dir=common_skills,
        builtin_skills_dir=tmp_path / "missing",
    )

    skills = loader.list_skills()

    assert skills == [
        {
            "name": "general",
            "path": str(tenant_skills / "general" / "SKILL.md"),
            "source": "tenant",
        }
    ]
    assert "tenant general" in loader.load_skill("general")


def test_tenant_skills_are_common_plus_tenant_custom_skills(tmp_path):
    common_skills = tmp_path / "common"
    tenant_skills = tmp_path / "tenant"
    _write_skill(common_skills, "general", "common general")
    _write_skill(common_skills, "pdf", "common pdf")
    _write_skill(tenant_skills, "brand_voice", "tenant brand voice")

    loader = SkillsLoader(
        workspace=tmp_path,
        tenant_skills_dir=tenant_skills,
        common_skills_dir=common_skills,
        builtin_skills_dir=tmp_path / "missing",
    )

    skills_by_name = {skill["name"]: skill for skill in loader.list_skills()}

    assert set(skills_by_name) == {"general", "pdf", "brand_voice"}
    assert skills_by_name["general"]["source"] == "common"
    assert skills_by_name["pdf"]["source"] == "common"
    assert skills_by_name["brand_voice"]["source"] == "tenant"


def test_skills_without_execute_are_not_listed_as_executable(tmp_path):
    common_skills = tmp_path / "common"
    _write_skill(common_skills, "image_gen", "image generation")
    _write_skill_without_execute(common_skills, "image_resize", "image resize")

    loader = SkillsLoader(
        workspace=tmp_path,
        tenant_skills_dir=tmp_path / "tenant",
        common_skills_dir=common_skills,
        builtin_skills_dir=tmp_path / "missing",
    )

    skills_by_name = {skill["name"]: skill for skill in loader.list_skills()}
    validation = loader.validate_skills()

    assert set(skills_by_name) == {"image_gen"}
    assert any(
        error["skill"] == "image_resize" and "scripts/main.py" in error["reason"]
        for error in validation["errors"]
    )
