
FROM runpod/pytorch:2.1.0-py3.10-cuda11.8.0-devel-ubuntu22.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN mkdir /app
WORKDIR /app

# Install OS dependencies
RUN apt update && apt install -y libpython3.10-dev

# Install Python dependencies
RUN pip install --upgrade pip && \
    pip install huggingface_hub runpod
RUN git clone https://github.com/turboderp/exllamav2.git
RUN cd exllamav2 && \
     TORCH_CUDA_ARCH_LIST=Turing python setup.py install --user

COPY handler.py /app/handler.py

ENV PYTHONPATH=/app/exllamav2
ENV MODEL_REPO=""
ENV PROMPT_PREFIX=""
ENV PROMPT_SUFFIX=""
ENV HUGGINGFACE_HUB_CACHE="/runpod-volume/huggingface-cache/hub"
ENV TRANSFORMERS_CACHE="/runpod-volume/huggingface-cache/hub"

ENTRYPOINT ["python", "-u", "/app/handler.py"]
