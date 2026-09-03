/*
 * Par de <select> Estado + Cidade em cascata, usando a API pública do IBGE
 * pra listar os municípios do estado escolhido (sem precisar embutir uma
 * lista de ~5.570 cidades no código). Reaproveitado em qualquer formulário
 * que precise dos dois campos -- ver uso em editar_perfil.html e index.html.
 */
window.EstadoCidade = (function () {
    var cachePorUf = {};

    function buscarCidades(uf) {
        if (cachePorUf[uf]) return Promise.resolve(cachePorUf[uf]);
        return fetch(
            "https://servicodados.ibge.gov.br/api/v1/localidades/estados/" + uf + "/municipios?orderBy=nome"
        )
            .then(function (resposta) {
                if (!resposta.ok) throw new Error("Falha ao buscar cidades do IBGE");
                return resposta.json();
            })
            .then(function (dados) {
                var nomes = dados.map(function (municipio) { return municipio.nome; });
                cachePorUf[uf] = nomes;
                return nomes;
            });
    }

    function montar(opcoes) {
        var selectEstado = opcoes.estadoSelect;
        var selectCidade = opcoes.cidadeSelect;
        var placeholderCidade = opcoes.placeholderCidade || "Selecione a cidade";
        var placeholderSemEstado = opcoes.placeholderSemEstado || "Selecione o estado primeiro";

        function preencherCidades(uf, manterValor) {
            if (!uf) {
                selectCidade.innerHTML = "";
                var optSemEstado = document.createElement("option");
                optSemEstado.value = "";
                optSemEstado.textContent = placeholderSemEstado;
                selectCidade.appendChild(optSemEstado);
                selectCidade.disabled = true;
                return Promise.resolve();
            }

            selectCidade.disabled = true;
            selectCidade.innerHTML = "";
            var optCarregando = document.createElement("option");
            optCarregando.value = "";
            optCarregando.textContent = "Carregando cidades...";
            selectCidade.appendChild(optCarregando);

            return buscarCidades(uf)
                .then(function (nomes) {
                    selectCidade.innerHTML = "";
                    var optVazia = document.createElement("option");
                    optVazia.value = "";
                    optVazia.textContent = placeholderCidade;
                    selectCidade.appendChild(optVazia);

                    var encontrouValorAtual = false;
                    nomes.forEach(function (nome) {
                        var opt = document.createElement("option");
                        opt.value = nome;
                        opt.textContent = nome;
                        if (manterValor && nome.toLowerCase() === manterValor.toLowerCase()) {
                            encontrouValorAtual = true;
                        }
                        selectCidade.appendChild(opt);
                    });

                    if (manterValor && !encontrouValorAtual) {
                        // Cidade salva não bate com a lista oficial do IBGE (dado
                        // antigo, digitado à mão antes desse campo virar select) --
                        // mantém como opção extra pra não perder a informação.
                        var optExtra = document.createElement("option");
                        optExtra.value = manterValor;
                        optExtra.textContent = manterValor + " (cadastro anterior)";
                        selectCidade.appendChild(optExtra);
                    }
                    if (manterValor) { selectCidade.value = manterValor; }
                    selectCidade.disabled = false;
                })
                .catch(function () {
                    selectCidade.innerHTML = "";
                    var optErro = document.createElement("option");
                    optErro.value = "";
                    optErro.textContent = "Não foi possível carregar as cidades agora";
                    selectCidade.appendChild(optErro);
                    selectCidade.disabled = false;
                });
        }

        selectEstado.addEventListener("change", function () {
            preencherCidades(selectEstado.value, null);
        });

        var carregamentoInicial = preencherCidades(selectEstado.value, opcoes.cidadeInicial || "");

        return {
            definir: function (uf, nomeCidade) {
                selectEstado.value = uf;
                return preencherCidades(uf, nomeCidade);
            },
            carregamentoInicial: carregamentoInicial,
        };
    }

    var NOMES_ESTADOS = {
        "acre": "AC", "alagoas": "AL", "amapá": "AP", "amapa": "AP", "amazonas": "AM",
        "bahia": "BA", "ceará": "CE", "ceara": "CE", "distrito federal": "DF",
        "espírito santo": "ES", "espirito santo": "ES", "goiás": "GO", "goias": "GO",
        "maranhão": "MA", "maranhao": "MA", "mato grosso": "MT", "mato grosso do sul": "MS",
        "minas gerais": "MG", "pará": "PA", "para": "PA", "paraíba": "PB", "paraiba": "PB",
        "paraná": "PR", "parana": "PR", "pernambuco": "PE", "piauí": "PI", "piaui": "PI",
        "rio de janeiro": "RJ", "rio grande do norte": "RN", "rio grande do sul": "RS",
        "rondônia": "RO", "rondonia": "RO", "roraima": "RR", "santa catarina": "SC",
        "são paulo": "SP", "sao paulo": "SP", "sergipe": "SE", "tocantins": "TO",
    };

    function ufPorNomeEstado(nomeCompleto) {
        if (!nomeCompleto) return "";
        return NOMES_ESTADOS[nomeCompleto.trim().toLowerCase()] || "";
    }

    return { montar: montar, ufPorNomeEstado: ufPorNomeEstado };
})();
