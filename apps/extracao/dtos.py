"""Registros de identidade e vínculos transportados em memória."""

from dataclasses import dataclass, field


@dataclass
class VinculoServidor:
    """Um vínculo funcional vigente de um servidor no SE1426.

    Um servidor pode ter múltiplos vínculos simultâneos e independentes
    (cargo base, cargo sobreposto/comissionado e função/atividade —
    inclusive mais de um cargo base ao mesmo tempo), cada um com sua
    própria unidade e DRE — não há prevalência entre tipos, todos são
    fatos distintos do domínio.
    """

    tipo_vinculo: str  # cargo_base | cargo_sobreposto | funcao_atividade
    codigo_vinculo_origem: str
    cargo_codigo: str | None = None
    cargo_nome: str | None = None
    unidade_codigo: str | None = None
    unidade_nome: str | None = None
    dre_codigo: str | None = None
    situacao: str | None = None
    data_inicio: str | None = None
    vigente: bool = True


@dataclass
class RegistroIdentidade:
    """Representa um registro de identidade extraído de uma fonte legada.

    Trafega em memória pelo pipeline sem ser persistido.
    """

    fonte: str  # se1426 | coresso | eol_alunos
    tipo: str  # servidor | aluno | terceiro
    rf: str | None = None
    cpf: str | None = None
    nome: str | None = None
    email: str | None = None
    situacao: str | None = None
    cargo: str | None = None
    funcao: str | None = None
    lotacao: str | None = None
    lotacao_nome: str | None = None
    dre: str | None = None
    ue: str | None = None
    matricula: str | None = None
    cod_escola: str | None = None
    turma: str | None = None
    tipo_acesso: str | None = None
    vinculos: list[VinculoServidor] = field(default_factory=list)
    dados_extras: dict = field(default_factory=dict)
