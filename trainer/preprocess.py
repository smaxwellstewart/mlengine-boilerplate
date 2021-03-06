#!/usr/bin/python
import argparse
import datetime
import logging
import os
import sys
import datetime
import numpy as np

import apache_beam as beam
from apache_beam.io import filesystem
from apache_beam.io import tfrecordio
from apache_beam.metrics import Metrics

import tensorflow as tf
from tensorflow_transform.tf_metadata import dataset_schema
from tensorflow_transform import coders

from config import PROJECT_ID, DATA_DIR, TFRECORD_DIR
from util import schema

partition_train = Metrics.counter('partition', 'train')
partition_validation = Metrics.counter('partition', 'validation')
partition_test = Metrics.counter('partition', 'test')
examples_failed = Metrics.counter('build', 'failed')


def buildExample(raw_input):
    '''
    Build a dictionary that contains all the features&label to store as TFRecord
    Args:
      tuple: a tuple containing the data to build the example from
    Returns:
      a dictionary of features
    '''
    try:
        elements = raw_input.split(',')
        key = elements[0]
        label = float(elements[1])
        feat = [float(el) for el in elements[2:]]
        features = {
            'id': key,
            'label': label,
            'feat': feat,
        }
        yield features
    except Exception as e:
        examples_failed.inc()
        logging.error(e, exc_info=True)
        pass


def partition_fn(example, num_partitions):
    '''
    Deterministic partition function that partitions examples based on hashing
    Args:
      example: a dictionary with at least one key id
      num_partitions: number of partitions the function can return
    Returns:
      an integer representing the partition in which the example is put (based on the key id)
    '''
    distribution = [80, 10, 10]
    bucket = hash(str(example['id'])) % np.sum(distribution)
    if bucket < distribution[0]:
        partition_train.inc()
        return 0
    elif bucket < distribution[0] + distribution[1]:
        partition_validation.inc()
        return 1
    else:
        partition_test.inc()
        return 2


def parse_arguments(argv):
    '''Parse command line arguments.
    Args:
      argv: list of command line arguments including program name.
    Returns:
      The parsed arguments as returned by argparse.ArgumentParser.
    '''
    parser = argparse.ArgumentParser(
        description='Runs Preprocessing.')

    parser.add_argument(
        '--project_id',
        default=PROJECT_ID,
        help='The project to which the job will be submitted.')
    parser.add_argument(
        '--cloud', action='store_true', help='Run preprocessing on the cloud.')
    parser.add_argument(
        '--output_dir',
        default=TFRECORD_DIR,
        help=('Google Cloud Storage or Local directory in which '
              'to place outputs.'))
    args, _ = parser.parse_known_args(args=argv[1:])

    return args


def main(argv=None):
    '''Run Preprocessing as a Dataflow pipeline.'''
    args = parse_arguments(sys.argv if argv is None else argv)
    if args.cloud:
        logging.info('Start running in the cloud')
        options = {
            'runner': 'DataflowRunner',
            'job_name': ('mlengine-boilerplate-{}'.format(
                datetime.datetime.now().strftime('%Y%m%d%H%M%S'))),
            'staging_location': os.path.join(args.output_dir, 'staging'),
            'temp_location':
                os.path.join(args.output_dir, 'tmp'),
            'project':
                args.project_id,
            'zone': 'europe-west1-d',
            'autoscaling_algorithm': 'THROUGHPUT_BASED',
            'save_main_session': True,
            'setup_file':
                './setup.py',
        }
        pipeline_options = beam.pipeline.PipelineOptions(flags=[], **options)
        print(pipeline_options)
    else:
        pipeline_options = None

    train_coder = coders.ExampleProtoCoder(schema)

    p = beam.Pipeline(options=pipeline_options)

    examples = (p
                | 'ReadData' >> beam.io.ReadFromText(
                    DATA_DIR + '/*', skip_header_lines=1)
                | 'buildExamples' >> beam.FlatMap(lambda raw_input: buildExample(raw_input)))

    examples_split = examples | beam.Partition(
        partition_fn, 3)
    example_dict = {
        'train': examples_split[0],
        'validation': examples_split[1],
        'test': examples_split[2]
    }

    for part, examples in example_dict.items():
        _ = examples | part + '_writeExamples' >> tfrecordio.WriteToTFRecord(
            file_path_prefix=os.path.join(
                args.output_dir, part + '_examples'),
            compression_type=filesystem.CompressionTypes.GZIP,
            coder=train_coder,
            file_name_suffix='.gz')

    p.run()


if __name__ == '__main__':
    main()
