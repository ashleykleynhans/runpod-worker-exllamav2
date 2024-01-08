import os
import sys
import glob
import re
import codecs
import traceback
import runpod
from runpod.serverless.modules.rp_logger import RunPodLogger
from huggingface_hub import snapshot_download
from copy import copy
from typing import Generator, Union

from exllamav2 import(
    ExLlamaV2,
    ExLlamaV2Config,
    ExLlamaV2Cache,
    ExLlamaV2Tokenizer,
)

from exllamav2.generator import (
    ExLlamaV2BaseGenerator,
    ExLlamaV2StreamingGenerator,
    ExLlamaV2Sampler,
)


ESCAPE_SEQUENCE_RE = re.compile(r'''
    ( \\U........      # 8-digit hex escapes
    | \\u....          # 4-digit hex escapes
    | \\x..            # 2-digit hex escapes
    | \\[0-7]{1,3}     # Octal escapes
    | \\N\{[^}]+\}     # Unicode characters by name
    | \\[\\'"abfnrtv]  # Single-character escapes
    )''', re.UNICODE | re.VERBOSE)


logger = RunPodLogger()
tokenizer = None
generator = None
default_settings = None
model = None
cache = None


def decode_escapes(s):
    def decode_match(match):
        return codecs.decode(match.group(0), 'unicode-escape')

    return ESCAPE_SEQUENCE_RE.sub(decode_match, s)


prompt_prefix = decode_escapes(os.getenv('PROMPT_PREFIX', ''))
prompt_suffix = decode_escapes(os.getenv('PROMPT_SUFFIX', ''))


def load_model():
    global generator, default_settings, tokenizer, model, cache

    if not generator:
        model_directory = snapshot_download(
            repo_id=os.environ['MODEL_REPO'],
            revision=os.getenv('MODEL_REVISION', 'main')
        )

        st_pattern = os.path.join(model_directory, '*.safetensors')
        st_files = glob.glob(st_pattern)

        if not st_files:
            raise ValueError(f'No safetensors files found in {model_directory}')

        model_path = st_files[0]

        config = ExLlamaV2Config()
        config.model_dir = model_directory
        config.prepare()

        gpu_split = os.getenv('GPU_SPLIT', '')

        if gpu_split:
            config.set_auto_map(gpu_split)
            config.gpu_peer_fix = True

        alpha_value = int(os.getenv('ALPHA_VALUE', '1'))
        config.max_seq_len = int(os.getenv('MAX_SEQ_LEN', '2048'))

        if alpha_value != 1:
            config.alpha_value = alpha_value
            config.calculate_rotary_embedding_base()

        model = ExLlamaV2(config)
        logger.info(f'Loading model: {model_path}')
        cache = ExLlamaV2Cache(model, lazy=True)
        model.load_autosplit(cache)

        tokenizer = ExLlamaV2Tokenizer(config)
        generator = ExLlamaV2BaseGenerator(model, cache, tokenizer)

        settings = ExLlamaV2Sampler.Settings()
        settings.temperature = 0.85
        settings.top_k = 50
        settings.top_p = 0.8
        settings.top_a = 0.0
        settings.token_repetition_penalty = 1.05
        settings.disallow_tokens(tokenizer, [tokenizer.eos_token_id])

        default_settings = {
            k: getattr(settings, k) for k in dir(settings) if k[:2] != '__'
        }

    return generator, tokenizer, default_settings


def generate_with_streaming(prompt, settings, max_new_tokens):
    global generator, tokenizer, model, cache

    # Tokenizing the input
    input_ids = tokenizer.encode(prompt)

    # Calculate number of tokens in the prompt
    prompt_tokens = input_ids.shape[-1]

    generator = ExLlamaV2StreamingGenerator(model, cache, tokenizer)
    generator.warmup()
    generator.set_stop_conditions([])
    generator.begin_stream(input_ids, settings)
    generated_tokens = 0

    while True:
        chunk, eos, _ = generator.stream()
        generated_tokens += 1
        print(chunk, end='')
        yield chunk
        sys.stdout.flush()

        if eos or generated_tokens == max_new_tokens:
            break


def inference(event) -> Union[str, Generator[str, None, None]]:
    global generator, default_settings

    try:
        job_input = event['input']

        if not job_input:
            raise ValueError('No input provided')

        prompt: str = job_input.pop('prompt_prefix', prompt_prefix) + \
            job_input.pop('prompt') + \
            job_input.pop('prompt_suffix', prompt_suffix)

        max_new_tokens = job_input.pop('max_new_tokens', 100)
        stream: bool = job_input.pop('stream', False)
        settings = copy(default_settings)
        settings.update(job_input)
        sampler_settings = ExLlamaV2Sampler.Settings()

        for key, value in settings.items():
            setattr(sampler_settings, key, value)

        if stream:
            output: Union[str, Generator[str, None, None]] = generate_with_streaming(
                prompt,
                sampler_settings,
                max_new_tokens
            )

            for res in output:
                yield res
        else:
            output_text = generator.generate_simple(prompt, sampler_settings, max_new_tokens)
            yield output_text[len(prompt):]
    except Exception as e:
        logger.error(f'An exception was raised: {e}')

        return {
            'error': traceback.format_exc(),
            'refresh_worker': True
        }


if __name__ == '__main__':
    generator, tokenizer, default_settings = load_model()
    generator.warmup()
    logger.info('Starting ExLlamaV2 serverless worker with streaming enabled.')
    runpod.serverless.start(
        {
            'handler': inference
        }
    )
