from flask import Blueprint, jsonify, request
from models import db, Pedido, ItemPedido, Cliente, Endereco, NotaFiscal, Produto
from datetime import datetime
import pytz
import os

BRASILIA = pytz.timezone("America/Sao_Paulo")

fiscal_bp = Blueprint('fiscal', __name__, url_prefix='/api/fiscal')

API_KEY = os.getenv("FISCAL_API_KEY")

@fiscal_bp.before_request
def proteger_rotas():
    chave_recebida = request.headers.get("X-API-KEY")

    if not API_KEY:
        return jsonify({"erro": "API key não configurada no servidor"}), 500

    if chave_recebida != API_KEY:
        return jsonify({"erro": "Acesso não autorizado"}), 401
		
# =====================================================
# 📦 UTIL: montar payload do pedido
# =====================================================
def montar_payload(pedido, itens, tipo):

    cliente = Cliente.query.get(pedido.cliente_id)
    endereco = pedido.endereco

    dentro_estado = endereco.estado == "MG"

    def definir_cfop():
        if tipo == "faturamento":
            return "5922" if dentro_estado else "6922"
        elif tipo == "remessa":
            return "5117" if dentro_estado else "6117"
        else:
            raise ValueError("Tipo inválido")

    def definir_csosn(item=None):
        if tipo == "faturamento":
            return "900"
        elif tipo == "remessa":
            return "101"
        else:
            raise ValueError("Tipo inválido")

    cfop = definir_cfop()

    return {
        "pedido_id": pedido.id,
        "tipo": tipo,

        "cliente": {
            "nome": cliente.nome,
            "cpf_cnpj": cliente.cpf_cnpj,
            "email": cliente.email,
            "telefone": cliente.telefone
        },

        "endereco": {
            "rua": endereco.rua,
            "numero": endereco.numero,
            "complemento": endereco.complemento,
            "bairro": endereco.bairro,
            "cidade": endereco.cidade,
            "estado": endereco.estado,
            "cep": endereco.cep
        },

        "itens": [
            {
                "produto_id": item.produto_id,
                "nome": item.produto.nome if item.produto else None,

                "quantidade": item.quantidade,
                "valor_unitario": item.preco_unitario,
                "subtotal": item.quantidade * item.preco_unitario,

                "ncm": item.produto.ncm if item.produto else None,
                "cfop": cfop,
                "csosn": definir_csosn(item),
                "unidade": item.produto.unidade if item.produto else "UN"
            }
            for item in itens
        ],

        "total": pedido.total,
        "frete": pedido.frete,

        "informacoes_adicionais": (
            "Nota Fiscal de Simples Faturamento em venda para entrega futura, "
            "emitida nos termos do artigo 215 do anexo VIII do RICMS/MG"
            if tipo == "faturamento"
            else f"Remessa referente ao pedido {pedido.id}"
        )
    }


# =====================================================
# 📥 ENDPOINT 1 — FATURAMENTO
# =====================================================
@fiscal_bp.route('/pedido/<int:pedido_id>/faturamento', methods=['GET'])
def obter_faturamento(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    itens = ItemPedido.query.filter_by(pedido_id=pedido_id).all()

    payload = montar_payload(pedido, itens, tipo="faturamento")
    return jsonify(payload)


# =====================================================
# 📦 ENDPOINT 2 — REMESSA
# =====================================================
@fiscal_bp.route('/pedido/<int:pedido_id>/remessa', methods=['GET'])
def obter_remessa(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    itens = ItemPedido.query.filter_by(pedido_id=pedido_id).all()

    payload = montar_payload(pedido, itens, tipo="remessa")
    return jsonify(payload)


# =====================================================
# 📬 ENDPOINT 3 — RETORNO DA CONTABILIDADE
# =====================================================
@fiscal_bp.route('/retorno', methods=['POST'])
def retorno_nf():
    data = request.json

    pedido_id = data.get('pedido_id')
    tipo = data.get('tipo')

    if not pedido_id or not tipo:
        return jsonify({"erro": "pedido_id e tipo são obrigatórios"}), 400

    nota = NotaFiscal.query.filter_by(
        pedido_id=pedido_id,
        tipo=tipo
    ).first()

    if not nota:
        nota = NotaFiscal(
            pedido_id=pedido_id,
            tipo=tipo
        )
        db.session.add(nota)

    nota.numero = data.get('numero')
    nota.chave_acesso = data.get('chave')
    nota.xml = data.get('xml')
    nota.pdf_url = data.get('pdf_url')
    nota.status = data.get('status', 'emitida')
    nota.data_emissao = datetime.now(BRASILIA)
    nota.erro = data.get('erro')

    db.session.commit()

    return jsonify({"sucesso": True})


# =====================================================
# 🧪 ENDPOINT 4 — LISTAR PEDIDOS APROVADOS
# =====================================================
@fiscal_bp.route('/pedidos_pagos', methods=['GET'])
def listar_pedidos_pagos():
    try:
        pedidos = Pedido.query.filter_by(
            status_pagamento='approved'
        ).all()

        lista_final = []

        for p in pedidos:
            cliente = Cliente.query.filter(
                Cliente.id == p.cliente_id,
                Cliente.ativo.is_(True)
            ).first()

            if not cliente:
                continue

            endereco = p.endereco

            itens_db = ItemPedido.query.filter_by(
                pedido_id=p.id
            ).all()

            itens = []
            subtotal = 0

            for item in itens_db:
                produto = Produto.query.get(item.produto_id)

                total_item = round(
                    item.quantidade * item.preco_unitario, 2
                )

                subtotal += total_item

                itens.append({
                    "item_id": item.id,
                    "produto_id": item.produto_id,
                    "descricao": produto.nome if produto else f"Produto {item.produto_id}",
                    "quantidade": item.quantidade,
                    "valor_unitario": round(item.preco_unitario, 2),
                    "valor_total": total_item,
                    "tamanho": item.tamanho,
                    "ncm": getattr(produto, "ncm", "61091000"),
                    "cfop": "5102",
                    "csosn": "102"
                })

            total_nota = round(
                subtotal + (p.frete or 0) - (p.desconto_aplicado or 0), 2
            )

            dados = {
                "pedido": {
                    "id": p.id,
                    "numero": p.id,
                    "data_criacao": p.data_pedido.isoformat() if p.data_pedido else None,
                    "status": p.status,
                    "status_pagamento": p.status_pagamento
                },

                "destinatario": {
                    "id": cliente.id,
                    "nome": cliente.nome,
                    "email": cliente.email,
                    "telefone": cliente.telefone,
                    "cpf_cnpj": cliente.cpf_cnpj
                },

                "endereco": {
                    "rua": endereco.rua if endereco else None,
                    "numero": endereco.numero if endereco else None,
                    "complemento": endereco.complemento if endereco else None,
                    "bairro": endereco.bairro if endereco else None,
                    "cidade": endereco.cidade if endereco else None,
                    "estado": endereco.estado if endereco else None,
                    "cep": endereco.cep if endereco else None
                },

                "itens": itens,

                "totais": {
                    "subtotal": round(subtotal, 2),
                    "frete": round(p.frete or 0, 2),
                    "desconto": round(p.desconto_aplicado or 0, 2),
                    "total_nota": total_nota
                },

                "codRastreio": p.codRastreio,

                "sugestao_nota": (
                    "faturamento"
                    if p.tipo_entrega == "retirada"
                    else "faturamento_e_remessa"
                )
            }

            lista_final.append(dados)

        return jsonify(lista_final), 200

    except Exception as e:
        return jsonify({"erro": str(e)}), 500

# =====================================================
# 🧪 ENDPOINT 5 — MUDAR STATUS DO PEDIDO
# =====================================================
@fiscal_bp.route('/pedido/<int:pedido_id>/status', methods=['PUT', 'POST'])
def atualizar_status_pedido(pedido_id):
    pedido = Pedido.query.get_or_404(pedido_id)
    data = request.json
    
    novo_status = data.get('status')
    if not novo_status:
        return jsonify({"erro": "O campo 'status' é obrigatório"}), 400
        
    pedido.status = novo_status
    db.session.commit()
    
    return jsonify({"sucesso": True, "pedido_id": pedido.id, "novo_status": pedido.status})



# =====================================================
# 🧪 ENDPOINT TESTE — TESTE MANUAL
# =====================================================
@fiscal_bp.route('/emitir/<int:pedido_id>/<tipo>', methods=['POST'])
def emitir_manual(pedido_id, tipo):
    if tipo not in ['faturamento', 'remessa']:
        return jsonify({"erro": "tipo inválido"}), 400

    # evita duplicidade
    existente = NotaFiscal.query.filter_by(
        pedido_id=pedido_id,
        tipo=tipo
    ).first()

    if existente:
        return jsonify({
            "msg": "Nota já existe",
            "nota": existente.to_dict()
        })

    nota = NotaFiscal(
        pedido_id=pedido_id,
        tipo=tipo,
        status='pendente'
    )

    db.session.add(nota)
    db.session.commit()

    return jsonify({
        "msg": f"NF de {tipo} criada (simulação)",
        "nota": nota.to_dict()
    })
