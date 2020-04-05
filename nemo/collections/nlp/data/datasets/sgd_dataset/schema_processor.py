"""
Some parts of this code were adapted from 
https://github.com/google-research/google-research/tree/master/schema_guided_dst
"""

import collections
import os

import numpy as np
import torch

import nemo
from nemo.collections.nlp.data.datasets.sgd_dataset.schema_embedding_dataset import SchemaEmbeddingDataset
from nemo.collections.nlp.nm.data_layers.bert_inference_datalayer import BertInferDataLayer

__all__ = ['SchemaPreprocessor']


def concatenate(lists):
    """
    Helper function for inference
    """
    return np.concatenate([t.cpu() for t in lists])


class SchemaPreprocessor:
    """ 
    Convert the raw data to the standard format supported by
    StateTrackingSGDData.
    
    Args:
        data_dir (str) - Directory for the downloaded DSTC8 data, which contains
            the dialogue files and schema files of all datasets (eg train, dev)
        dialogues_example_dir (str) - Directory where preprocessed DSTC8 dialogues are stored
        schema_embedding_dir (str) - Directory where .npy file for embedding of
            entities (slots, values, intents) in the dataset_split's
            schema are stored.
        task_name (str) - The name of the task to train
        vocab_file (str) - The path to BERT vocab file
        do_lower_case - (bool) - Whether to lower case the input text.
            Should be True for uncased models and False for cased models.
        max_seq_length (int) - The maximum total input sequence length after
            WordPiece tokenization. Sequences longer than this will be
            truncated, and sequences shorter than this will be padded."
        tokenizer - tokenizer
        bert_model - pretrained BERT model
        dataset_split (str) - Dataset split for training / prediction (train/dev/test)
        overwrite_dial_file (bool) - Whether to generate a new file saving
            the dialogue examples overwrite_schema_emb_file,
        bert_ckpt_dir (str) - Directory containing pre-trained BERT checkpoint
        nf - NeuralModuleFactory
    """

    def __init__(
        self,
        data_dir,
        schema_embedding_dir,
        max_seq_length,
        tokenizer,
        embedding_dim,
        bert_model,
        datasets,
        overwrite_schema_emb_files,
        bert_ckpt_dir,
        nf,
    ):
        self.schemas_dict = {}
        self._schema_embedding_dir = schema_embedding_dir
        for dataset_split in datasets:
            schema_embedding_file = self._get_schema_embedding_file_name(dataset_split)

            # Generate the schema embeddings if needed or specified
            master_device = not torch.distributed.is_initialized() or torch.distributed.get_rank() == 0
            if master_device and not os.path.exists(schema_embedding_file) or overwrite_schema_emb_files:
                nemo.logging.info(f"Start generating the schema embeddings for {dataset_split} dataset.")
                # create schema embedding if no file exists
                schema_json_path = os.path.join(data_dir, dataset_split, "schema.json")

                emb_datalayer = BertInferDataLayer(
                    dataset_type=SchemaEmbeddingDataset,
                    tokenizer=tokenizer,
                    max_seq_length=max_seq_length,
                    input_file=schema_json_path,
                    embedding_dim=embedding_dim,
                )

                input_ids, input_mask, input_type_ids = emb_datalayer()
                hidden_states = bert_model(
                    input_ids=input_ids, token_type_ids=input_type_ids, attention_mask=input_mask
                )

                evaluated_tensors = nf.infer(tensors=[hidden_states], checkpoint_dir=bert_ckpt_dir)

                hidden_states = [concatenate(tensors) for tensors in evaluated_tensors]
                emb_datalayer.dataset.save_embeddings(hidden_states, schema_embedding_file)

                nemo.logging.info(f"The schema embeddings saved at {schema_embedding_file}")
                nemo.logging.info("Finish generating the schema embeddings.")

    def get_schema_embeddings(self, dataset_split):
        schema_embedding_file = self._get_schema_embedding_file_name(dataset_split)

        if not os.path.exists(schema_embedding_file):
            raise ValueError(f"{schema_embedding_file} not found.")

        with open(schema_embedding_file, "rb") as f:
            schema_data = np.load(f, allow_pickle=True)
            f.close()

        # Convert from list of dict to dict of list
        schema_data_dict = collections.defaultdict(list)
        for service in schema_data:
            schema_data_dict["cat_slot_emb"].append(service["cat_slot_emb"])
            schema_data_dict["cat_slot_value_emb"].append(service["cat_slot_value_emb"])
            schema_data_dict["noncat_slot_emb"].append(service["noncat_slot_emb"])
            schema_data_dict["req_slot_emb"].append(service["req_slot_emb"])
            schema_data_dict["intent_emb"].append(service["intent_emb"])

        return schema_data_dict

    def _get_schema_embedding_file_name(self, dataset_split):
        return os.path.join(self._schema_embedding_dir, f"{dataset_split}_pretrained_schema_embedding.npy")
