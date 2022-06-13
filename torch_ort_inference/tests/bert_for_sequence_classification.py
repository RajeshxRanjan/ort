import argparse
from lib2to3.pgen2 import token
import os
import numpy as np
import time
import pandas as pd
import itertools

from transformers import AutoTokenizer
from transformers import AutoModelForSequenceClassification

import torch
from torch_ort import ORTInferenceModule, OpenVINOProviderOptions

ov_backend_precisions = {"CPU": ["FP32"], "GPU": ["FP32", "FP16"]}


def preprocess_input(tokenizer, sentences):
    # Tokenization & Input Formatting
    # Config: "do_lower_case": true, "model_max_length": 512
    inputs = []

    for sent in sentences:
        tokenized_inputs = tokenizer(
            sent,
            return_tensors="pt",
            padding='max_length',
            truncation=True)
        inputs.append(tokenized_inputs)

    return inputs


def infer(model, tokenizer, inputs):
    total_infer_time = 0
    results = {}

    # Run inference
    for i in range(len(inputs)):
        input_ids = (inputs[i])['input_ids']
        attention_masks = (inputs[i])['attention_mask']
        with torch.no_grad():
            # warm-up
            if i == 0:
               t0 = time.time()
               model(input_ids, attention_masks)
               print("warm up time:", time.time()-t0)
            # infer
            t0 = time.time()
            outputs = model(input_ids, attention_masks)
            t1 = time.time() - t0
            total_infer_time += t1

        # Get the "logits" output by the model. The "logits" are the output
        # values prior to applying an activation function like the softmax.
        logits = outputs.logits

        # Move logits
        logits = logits.detach().cpu().numpy()

        # predictions
        pred_flat = np.argmax(logits, axis=1).flatten()
        orig_sent = tokenizer.decode(input_ids[0],skip_special_tokens=True)
        results[orig_sent] = pred_flat[0]

    print("\n Top (20) Results: \n")
    count = 0
    for k, v in results.items():
        print("\t{!r} : {!r}".format(k, v))
        if count == 20:
            break
        count = count + 1
    print("\nInference time: {:.4f}s".format(total_infer_time))


def main():
    # 1. Basic setup
    parser = argparse.ArgumentParser(
        description="PyTorch BERT Sequence Classification Example"
    )
    parser.add_argument(
        "--pytorch-only",
        action="store_true",
        default=False,
        help="disables ONNX Runtime",
    )

    parser.add_argument("--input", type=str, default=None, help="input sentence")
    parser.add_argument(
        "--input-file",
        type=str,
        default=None,
        help="input file in .tsv format",
    )
    parser.add_argument(
        "--provider",
        type=str,
        help="ONNX Runtime Execution Provider",
    )
    parser.add_argument(
        "--backend", type=str, help="OpenVINO target device (CPU, GPU or MYRIAD)."
    )
    parser.add_argument(
        "--precision", type=str, help="OpenVINO target device precision (FP16 or FP32)"
    )

    args = parser.parse_args()

    # parameters validation
    if not args.pytorch_only:
        if args.provider is None:
            print("OpenVINOExecutionProvider is enabled with CPU and FP32 by default.")
            args.provider = "openvino"
        if args.provider == "openvino":
            if (args.backend is not None) and (
                args.backend not in list(ov_backend_precisions.keys())
            ):
                raise Exception(
                    "Invalid backend. Valid values are:",
                    list(ov_backend_precisions.keys()),
                )

            if (args.precision is not None) and (
                args.precision not in ov_backend_precisions[args.backend]
            ):
                raise Exception("Invalid precision for provided backend")

            if not (args.backend and args.precision):
                print(
                    "OpenVINOExecutionProvider is enabled with CPU and FP32 by default."
                    + " Please specify backend and precision to override.\n"
                )
                args.backend = "CPU"
                args.precision = "FP32"
        else:
            raise Exception("Invalid execution provider!!")

    # 2. Load Model
    # Pretrained model fine-tuned on CoLA dataset from huggingface model hub for grammar check
    model = AutoModelForSequenceClassification.from_pretrained(
        "textattack/bert-base-uncased-CoLA"
    )

    if not args.pytorch_only:
        provider_options = None
        if args.provider == "openvino":
            provider_options = OpenVINOProviderOptions(
                backend=args.backend, precision=args.precision
            )
        model = ORTInferenceModule(model, provider_options=provider_options)

    # Convert model for evaluation
    model.eval()

    # 3. Read input sentence(s)
    # Input can be a single sentence, list of single sentences in a .tsv file.
    if args.input is not None:
        sentences = [args.input]
    elif args.input_file is not None:
        if not os.path.exists(args.input_file):
            raise ValueError("Invalid input file path: %s" % args.input_file)
        df = pd.read_csv(
            args.input_file,
            delimiter="\t",
            header=None,
            names=["Id", "Sentence"],
            skiprows=1,
        )
        sentences = df.Sentence.values
    else:
        print("Input not provided! Using default input...")
        sentences = ["This is a sample input."]

    # 4. Load Tokenizer & Preprocess input sentences
    tokenizer = AutoTokenizer.from_pretrained("textattack/bert-base-uncased-CoLA")
    inputs = preprocess_input(tokenizer, sentences)

    # 6. Infer
    infer(model, tokenizer, inputs)


if __name__ == "__main__":
    main()
