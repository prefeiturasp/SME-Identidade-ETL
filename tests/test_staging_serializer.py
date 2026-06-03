from types import SimpleNamespace

from staging.serializers import DedupResultSerializer


class TestDedupResultSerializer:
    """Testes para os métodos auxiliares do DedupResultSerializer."""

    def test_get_loser_nome(self):
        """Deve retornar o nome do registro perdedor quando existente."""
        serializer = DedupResultSerializer()

        loser = SimpleNamespace(
            nome="LOSER",
            source="coresso",
        )

        obj = SimpleNamespace(loser=loser)

        assert serializer.get_loser_nome(obj) == "LOSER"

    def test_get_loser_nome_without_loser(self):
        """Deve retornar None quando não houver registro perdedor."""
        serializer = DedupResultSerializer()

        obj = SimpleNamespace(loser=None)

        assert serializer.get_loser_nome(obj) is None

    def test_get_loser_source(self):
        """Deve retornar a origem do registro perdedor quando existente."""
        serializer = DedupResultSerializer()

        loser = SimpleNamespace(
            nome="LOSER",
            source="coresso",
        )

        obj = SimpleNamespace(loser=loser)

        assert serializer.get_loser_source(obj) == "coresso"

    def test_get_loser_source_without_loser(self):
        """Deve retornar None quando não houver origem do registro perdedor."""
        serializer = DedupResultSerializer()

        obj = SimpleNamespace(loser=None)

        assert serializer.get_loser_source(obj) is None
