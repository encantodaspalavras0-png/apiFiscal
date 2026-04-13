from flask import Blueprint, jsonify, request
from models import db, Pedido, ItemPedido, Cliente, Endereco, NotaFiscal
from datetime import datetime
import pytz

BRASILIA = pytz.timezone("America/Sao_Paulo")

fiscal_bp = Blueprint('fiscal', __name__, url_prefix='/api/fiscal')


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
    # Busca pedidos aprovados
    pedidos = Pedido.query.filter_by(status_pagamento='approved').all()
    
    lista_final = []
    for p in pedidos:
        dados = p.to_dict()
       
        dados['sugestao_nota'] = 'faturamento' if p.tipo_entrega == 'retirada' else 'faturamento_e_remessa'
        lista_final.append(dados)
    
    return jsonify(lista_final)

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
