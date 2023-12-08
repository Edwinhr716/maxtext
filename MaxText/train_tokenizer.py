"""
 Copyright 2023 Google LLC
 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at
      https://www.apache.org/licenses/LICENSE-2.0
 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
"""

""" Train tokenizer
Example usage: python3 MaxText/train_tokenizer.py --dataset_path=gs://maxtext-dataset --dataset_name=c4/en:3.0.1
"""

import os
import tempfile
import argparse
import time
from typing import Tuple
from absl import logging

import tensorflow as tf
import tensorflow_datasets as tfds

from sentencepiece import SentencePieceTrainer
import jax


def _dump_chars_to_textfile(
    dataset: tf.data.Dataset,
    maxchars: int = int(1e7),
    data_keys=('text',)
) -> Tuple[str, int]:
  """Write part of a TFDS sentence dataset to lines in a text file.
  Args:
    dataset: tf.dataset containing string-data.
    maxchars: int: approximate number of characters to save from dataset.
    data_keys: Tuple[str]: what keys in dataset to dump from.
  Returns:
    name of temp file with dataset bytes, exact number of characters dumped.
  """
  char_count = 0
  ds_iter = dataset.as_numpy_iterator()
  with tempfile.NamedTemporaryFile(
      delete=False, prefix='/tmp/ds_chars') as outfp:
    while char_count < maxchars:
      example = next(ds_iter)
      for k in data_keys:
        line = example[k] + b'\n'
        char_count += len(line)
        outfp.write(line)
  return outfp.name, char_count

def _train_sentencepiece(dataset: tf.data.Dataset,
                         *,
                         vocab_size: int,
                         maxchars: int = int(1e7),
                         assets_path: str,
                         model_path: str,
                         model_type: str = 'unigram',
                         character_coverage: float = 1.0,
                         data_keys=('text',)):
  """Train SentencePiece tokenizer from subset of tf dataset.
  Args:
    dataset: tf.dataset
    vocab_size: int: size of vocab tokens to train.
    maxchars: int: number of characters to use for sentencepiece training.
    model_path: str: path of model file to save vocab model to.
    model_type: str: type of sentencepiece vocab to train.
    character_coverage: amount of characters covered by the model, good defaults
      are 0.9995 for languages with rich character set like Japanese or Chinese
      and 1.0 for other languages with small character set.
    data_keys: Tuple[str]: keys of dataset to use for training.
  Returns:
    path to the trained sentencepiece vocabulary model.
  """
  if model_path.startswith('gs://'):
    abs_model_path = model_path
  else:
    abs_model_path = os.path.abspath(os.path.expanduser(model_path))
    abs_assets_path = os.path.abspath(os.path.expanduser(assets_path))
  fname, _ = _dump_chars_to_textfile(
      dataset, maxchars=maxchars, data_keys=data_keys)
  with tempfile.NamedTemporaryFile(
      delete=False, prefix='/tmp/sp_tmp') as model_fp:
    pass  # we just want a prefix'd tmp-filename
  argstr = ' '.join([
      f'--input={fname}', f'--vocab_size={vocab_size}',
      f'--character_coverage={character_coverage}',
      f'--model_prefix={model_fp.name}', f'--model_type={model_type}'
  ])
  SentencePieceTrainer.Train(argstr)
  if jax.process_index() == 0:
    # Use an intermediate filename that is renamed to the target name to address
    # create and fill delays.
    copy_rename_path = abs_model_path + '.rntmp'
    if not model_path.startswith('gs://'):
      tf.io.gfile.makedirs(abs_assets_path)
    tf.io.gfile.copy(model_fp.name + '.model', copy_rename_path, overwrite=True)
    tf.io.gfile.rename(copy_rename_path, abs_model_path, overwrite=True)
    logging.info('copied %s to %s', model_fp.name + '.model', abs_model_path)
  else:
    while not tf.io.gfile.exists(abs_model_path):
      time.sleep(1)
    time.sleep(1)
  return abs_model_path

def train_tokenizer(dataset: tf.data.Dataset,
                      *,
                      assets_path: str,
                      vocab_path: str,
                      vocab_size: int,
                      max_corpus_chars: int,
                      data_keys: Tuple[str] = ('text',)):
  """tokenizer training function"""
  logging.info('SentencePiece vocab not found, building one from data.')
  vocab_path = _train_sentencepiece(
      dataset,
      vocab_size=vocab_size,
      maxchars=max_corpus_chars,
      assets_path=assets_path,
      model_path=vocab_path,
      data_keys=data_keys)
  logging.info('Model saved at %s', vocab_path)



def main():
  parser = argparse.ArgumentParser(
    description="Train tokenizer"
  )
  parser.add_argument(
      "--dataset_path",
      required=True,
      default="",
      help="Path to the dataset",
      type=str
  )
  parser.add_argument(
      "--dataset_name",
      required=True,
      default="",
      help="Name to the dataset",
      type=str
  )
  parser.add_argument(
    "--vocab_size",
    default=32_768,
    help="vocab size",
    type=int
  )
  parser.add_argument(
    "--max_corpus_chars",
    default=10_000_000,
    help="max corpus chars",
    type=int
  )
  parser.add_argument(
      "--assets_path",
      required=False,
      default="assets",
      help="Name to the dataset",
      type=str
  )
  parser.add_argument(
      "--vocab_model_name",
      required=False,
      default="tokenizer",
      help="Name to the dataset",
      type=str
  )
  args = parser.parse_args()
  os.environ["TFDS_DATA_DIR"] = args.dataset_path

  read_config = tfds.ReadConfig(
    shuffle_seed = 0,
  )
  train_ds_builder = tfds.builder(args.dataset_name)
  train_ds = train_ds_builder.as_dataset(split='train', read_config=read_config, shuffle_files=True)
  train_tokenizer(train_ds,
                assets_path=args.assets_path,
                vocab_path=os.path.join(args.assets_path, args.vocab_model_name),
                vocab_size=args.vocab_size,
                max_corpus_chars=args.max_corpus_chars)

if __name__ == '__main__':
  main()
