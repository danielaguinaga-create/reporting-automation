import pytest

from reporting_automation.config.client_import import import_clients_from_csv, slugify
from reporting_automation.config.client_registry import ClientRegistry

CSV_HEADER = "idCompanyMD,idCompany,CompanyName,CompanyFiscalName,CompanyIsActive"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Avanza Seguros", "avanza_seguros"),
        ("Ve por Más", "ve_por_mas"),
        ("Bloss Med", "bloss_med"),
        ("Meeting-doctors", "meeting_doctors"),
        ("5CN", "5cn"),
        ("", "cliente"),
    ],
)
def test_slugify(name, expected):
    assert slugify(name) == expected


def _write_csv(tmp_path, rows: list[str]):
    csv_path = tmp_path / "clientes.csv"
    csv_path.write_text("\n".join([CSV_HEADER, *rows]) + "\n", encoding="utf-8")
    return csv_path


def test_import_creates_clients_from_csv(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        [
            "80,498cb81c5ba7325f,Protec,Protec SA,true",
            "295,6336afc65b98ae17,Avanza Seguros,Avanza Seguros,true",
        ],
    )
    clients_dir = tmp_path / "clients"

    result = import_clients_from_csv(csv_path, clients_dir)

    assert sorted(result.created) == ["avanza_seguros", "protec"]
    assert result.skipped_inactive == []
    assert result.skipped_existing == []

    registry = ClientRegistry()
    registry.load(clients_dir)
    protec = registry.get_or_none("protec")
    assert protec is not None
    assert protec.bq_params == {"id_company": "498cb81c5ba7325f"}


def test_import_skips_inactive_by_default(tmp_path):
    csv_path = _write_csv(tmp_path, ["153,ced70042b07809c7,Abanca,Abanca,false"])
    clients_dir = tmp_path / "clients"

    result = import_clients_from_csv(csv_path, clients_dir)

    assert result.created == []
    assert result.skipped_inactive == ["Abanca"]


def test_import_includes_inactive_when_only_active_false(tmp_path):
    csv_path = _write_csv(tmp_path, ["153,ced70042b07809c7,Abanca,Abanca,false"])
    clients_dir = tmp_path / "clients"

    result = import_clients_from_csv(csv_path, clients_dir, only_active=False)

    assert result.created == ["abanca"]


def test_import_skips_rows_without_id_company_or_name(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        [
            "1,,SinIdCompany,SinIdCompany,true",
            "2,abc123,,SinNombre,true",
        ],
    )
    clients_dir = tmp_path / "clients"

    result = import_clients_from_csv(csv_path, clients_dir)

    assert result.created == []


def test_import_does_not_overwrite_existing_by_default(tmp_path):
    csv_path = _write_csv(tmp_path, ["80,498cb81c5ba7325f,Protec,Protec SA,true"])
    clients_dir = tmp_path / "clients"

    import_clients_from_csv(csv_path, clients_dir)
    result = import_clients_from_csv(csv_path, clients_dir)

    assert result.created == []
    assert result.skipped_existing == ["protec"]


def test_import_overwrites_when_requested(tmp_path):
    (tmp_path / "v1").mkdir()
    (tmp_path / "v2").mkdir()
    csv_path_v1 = _write_csv(tmp_path / "v1", ["80,old_id_company,Protec,Protec SA,true"])
    csv_path_v2 = _write_csv(tmp_path / "v2", ["80,new_id_company,Protec,Protec SA,true"])
    clients_dir = tmp_path / "clients"

    import_clients_from_csv(csv_path_v1, clients_dir)
    import_clients_from_csv(csv_path_v2, clients_dir, overwrite=True)

    registry = ClientRegistry()
    registry.load(clients_dir)
    assert registry.get_or_none("protec").bq_params == {"id_company": "new_id_company"}


def test_import_dedupes_colliding_slugs_with_id_suffix(tmp_path):
    csv_path = _write_csv(
        tmp_path,
        [
            "1,aaa111,Reale,Reale SA,true",
            "2,bbb222,Reale,Reale Blue,true",
        ],
    )
    clients_dir = tmp_path / "clients"

    result = import_clients_from_csv(csv_path, clients_dir)

    assert sorted(result.created) == ["reale", "reale_2"]
