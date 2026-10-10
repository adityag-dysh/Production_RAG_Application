# YOUR_RAG_APP

An enterprise-grade Retrieval-Augmented Generation (RAG) system designed for precision, scalability, and secure data access.

## Architecture Overview

This application leverages a sophisticated, modular architecture designed to handle complex queries across unstructured and structured enterprise data.

### Key Architectural Choices
*   **Vector Database (Pinecone):** A cloud-native, high-performance vector database utilized for storing document embeddings, ensuring low-latency retrieval.
*   **Hybrid Search (Dense + Sparse):** Combines semantic search (Dense embeddings for conceptual understanding) with keyword-based search (Sparse vectors/BM25 for exact term matching) to achieve high recall and precision.
*   **Reranking (Cohere):** Implements a cross-encoder reranker to refine top retrieval results, prioritizing relevance based on context before passing them to the generative model.
*   **LLM (Google Gemini):** Utilized for intelligent response generation, summarization, and query reasoning.
*   **Access Control (ACL):** A robust security layer that filters retrieved documents based on user permissions, ensuring sensitive data is only surfaced to authorized individuals.

---

## System Workflow

### 1. Data Ingestion (ETL Pipeline)
Documents are processed through the `etl_orchestrator`.
*   **Parsing:** Extracts text from varied sources (PDFs, Logs, Code, TXT).
*   **Chunking & Embedding:** Splits documents into manageable chunks and generates embeddings.
*   **Indexing:** Upserts vectors to Pinecone with associated metadata (including ACL permissions).

### 2. Retrieval & Generation Pipeline
1.  **Query Rewrite:** The user query is analyzed and optimized for retrieval.
2.  **Hybrid Search:** Performs concurrent Dense (embedding) and Sparse (keyword) searches in Pinecone.
3.  **ACL Filtering:** Retrieved document chunks are filtered against the user’s ACL profile.
4.  **Reranking:** The top $N$ results are sent to the **Cohere** Reranker for final contextual relevance sorting.
5.  **Generation:** Context-enriched prompt + user query sent to **Google Gemini** for final answer generation.

---

## Getting Started

### Prerequisites
*   Python 3.10+
*   Virtual Environment tool (`venv` or `conda`)
*   API Keys: Pinecone, Cohere, Google Gemini

### Installation

1.  **Clone the repository:**
    ```bash
    git clone <your-repo-url>
    cd YOUR_RAG_APP
    ```

2.  **Setup Virtual Environment:**
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # Linux/Mac
    source venv/bin/activate
    ```

3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

### Environment Configuration
Create a `.env` file in the root directory (do not commit this file to version control):

```env
# API Keys
PINECONE_API_KEY=your_key_here
COHERE_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here

# Database Config
PINECONE_INDEX_NAME=your_index_name

# App Config
LOG_LEVEL=INFO
```

### Running the Application
To start the FastAPI backend server:

```bash
uvicorn app.main:app --reload
```

The API documentation will be available at `http://127.0.0.1:8000/docs`.

---

## Project Structure

```text
├── app/              # Main application logic
│   ├── api/          # Endpoints and Schema definitions
│   ├── core/         # Config, Security, ACL, Logging
│   └── services/     # Modular business logic (RAG, Embeddings, Storage)
├── data/             # Ingestion source documents (e.g., blobs)
├── evals/            # Evaluation scripts and datasets
├── prompts/          # Jinja2 prompt templates
├── scripts/          # Database seeding and utility scripts
├── static/           # Frontend assets
└── tests/            # Test suite
```
