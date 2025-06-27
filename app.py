import os
import glob
import sqlite3
import re
import yaml
from flask import Flask, request, jsonify

# LangChain
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain.chains import ConversationalRetrievalChain
from langchain.memory import ConversationBufferMemory
from langchain.docstore.document import Document
from langchain.schema import SystemMessage

# Arquivo de configuração
CONFIG_FILE = "config.yaml"
with open(CONFIG_FILE, "r") as file:
    config = yaml.safe_load(file)

# Lê chave openai e armazena em variável de ambiente
os.environ["OPENAI_API_KEY"] = config["api_key"]["key"]

# Nome do banco de dados para dados de suporte
DATABASE_PATH = "atendimentos.db"

# Flash
app = Flask(__name__)

# Memória e Contexto, usa Dicionário do Python
client_memories = {}  # conversa
client_context = {}  # número do atendimento do cliente - pode não ser usado

# Conecta com bd e retorna dados como dicionário
def get_db_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row  
    return conn

# Extrai número do atendimento da pergunta
# Usamos expressão regular
def extrair_id_atendimento(question):
    match = re.search(r'atendimento\s*(?:número|de número)?\s*(\d+)', question, re.IGNORECASE)
    return int(match.group(1)) if match else None

# Se é sobre atendimento retorna a resposta
def buscar_atendimento(question, client_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Busca id do atendimento, pode não existir
    atendimento_id = extrair_id_atendimento(question)
    if atendimento_id:
        cursor.execute("SELECT * FROM atendimentos WHERE id = ?", (atendimento_id,))
        atendimento = cursor.fetchone()
        if atendimento:
            # Guarda atendimento no contexto do usuário
            client_context[client_id] = atendimento_id  
            return f"Olá {atendimento['cliente_nome']}, o status do atendimento {atendimento_id} é {atendimento['status']}."
        return f"Não encontrei o atendimento número {atendimento_id}. Pode verificar se o número está correto?"

    # Se fez pergunta sem citar atendimento, usa último contexto
    if client_id in client_context:
        atendimento_id = client_context[client_id]
        cursor.execute("SELECT * FROM atendimentos WHERE id = ?", (atendimento_id,))
        atendimento = cursor.fetchone()
        if atendimento:
            if "defeito" in question.lower():
                return f"O defeito registrado no atendimento {atendimento_id} foi: {atendimento['defeito']}."
            if "descrição" in question.lower():
                return f"A descrição do atendimento {atendimento_id} é: {atendimento['descricao']}."
            if "data" in question.lower():
                return f"A data do atendimento {atendimento_id} foi {atendimento['data']}."
            if "status" in question.lower():
                return f"O status do atendimento {atendimento_id} é {atendimento['status']}."

    # Se for uma pergunta genérica de atendimento
    if "atendimento" in question.lower():
        return "Olá, para Informações sobre atendimento é preciso o número do atendimento"
    
    return None

# carrega documentos e retorna uma lista 
def load_documents(folder_path):
    documents = []
    for filepath in glob.glob(os.path.join(folder_path, "*.txt")):
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            documents.append(Document(page_content=content, metadata={"source": filepath}))
    return documents

# Carrega os documentos
documents = load_documents("documents")

# Cria embeddings com API da OpenAI
embeddings = OpenAIEmbeddings()

# Cria vectorstore com FAISS
vectorstore = FAISS.from_documents(documents, embeddings)

# Inicializa o modelo de chat com a mensagem do sistema
# Temperatura zero = respostas determinísticas
model = config["model"]["name"]
chat = ChatOpenAI(model=model, temperature=0)


# Definir o comportamento do agente
#    "Você é um assistente virtual especializado no atendimento ao cliente para uma renomada marca de relógios. "
#    "Sua função é responder perguntas sobre especificações dos produtos, garantias, manutenções e suporte técnico. "
##    "Além disso, você também fornece informações sobre atendimentos na assistência técnica, incluindo status, defeitos relatados e datas dos atendimentos. "
# #   "Responda sempre de maneira clara e objetiva, priorizando a precisão das informações."
#    "Caso a pergunta não esteja relacionada ao atendimento ao cliente, especificações de relógios, garantias, manutenções ou suporte técnico, responda: 'Desculpe, mas não posso responder a esse tipo de pergunta.'"
system_message = SystemMessage(content=(
    "Você é um assistente virtual especializado no atendimento ao cliente para uma renomada marca que trabalha com fornecimento de um produtdo para prática do esporte drone soccer. "
    "Sua função é responder perguntas sobre especificações dos produtos, modelo do negócio, garantias, manutenções e suporte técnico. "
    "Responda sempre de maneira clara e objetiva, priorizando a precisão das informações."
    "Caso a pergunta não esteja relacionada ao atendimento as especificações de produdo, do modelo de negógio, manutenções ou suporte técnico, responda: 'Desculpe, mas não posso responder a esse tipo de pergunta.'"
))

# Cria retriever a partir do vectorstore
retriever = vectorstore.as_retriever()


# endpoint padrão que recebe pergunta e client_id
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    if not data or "client_id" not in data or "question" not in data:
        return jsonify({"error": "Requisição inválida. Forneça 'client_id' e 'question'."}), 400

    client_id = data["client_id"]
    question = data["question"]

    # Verifica se é sobre atendimento
    atendimento_resposta = buscar_atendimento(question, client_id)
    if atendimento_resposta:
        return jsonify({"answer": atendimento_resposta})

# Recupera ou cria a memória de conversa
    if client_id not in client_memories:
        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        memory.chat_memory.messages.append(system_message)
        client_memories[client_id] = memory
    else:
        memory = client_memories[client_id]

       
    ## add

    # Cria a cadeia de conversação com Retrieval
    qa_chain = ConversationalRetrievalChain.from_llm(
        llm=chat,
        retriever=retriever,
        memory=memory
    )

    # Executa a cadeia com a pergunta do usuário
    result = qa_chain.invoke({"question": question})
    answer = result.get("answer", "")

    return jsonify({"answer": answer})

if __name__ == '__main__':
    # Servidor Flask na porta 5000
    app.run(host='0.0.0.0', port=5000, debug=True)
