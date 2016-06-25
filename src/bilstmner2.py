import os
import gc
import random
from collections import Counter
from tempfile import NamedTemporaryFile

import numpy as np
from pycnn import (Model, AdamTrainer, LSTMBuilder, renew_cg, lookup, noise, parameter, concatenate,
                   tanh, softmax, pickneglogsoftmax, esum)

from nertagger.tag_scheme import BILOU

from parse import parse_words, split_words_to_sentences, format_words
from indexer import Indexer


random.seed(1)


EVAL_NER_CMD = '/Users/konix/Workspace/nertagger/src/conlleval < {test_file}'

TRAIN_FILE_PATH = '/Users/konix/Workspace/nertagger/data/eng.train'
DEV_FILE_PATH = '/Users/konix/Workspace/nertagger/data/eng.testa'
TEST_FILE_PATH = '/Users/konix/Workspace/nertagger/data/eng.testb'


TAG_SCHEME = BILOU


class BiLstmNerTagger(object):
    WORD_DIM = 300
    LSTM_DIM = 100

    HIDDEN_DIM = 150

    def __init__(self, word_indexer, tag_indexer, external_word_embeddings=None):
        self.word_indexer = word_indexer
        self._unk_word_index = word_indexer.index_object('_UNK_')
        self.tag_indexer = tag_indexer

        model = Model()
        model.add_lookup_parameters("word_lookup", (len(word_indexer), self.WORD_DIM))

        if external_word_embeddings:
            word_lookup = model["word_lookup"]
            for idx in xrange(len(word_indexer)):
                word = word_indexer.get_object(idx)
                if word in external_word_embeddings:
                    word_lookup.init_row(idx, external_word_embeddings[word])

        self.param_hidden = model.add_parameters("HID", (self.HIDDEN_DIM, self.LSTM_DIM*2))
        self.param_out = model.add_parameters("OUT", (len(tag_indexer), self.HIDDEN_DIM))

        self.builders = [
            LSTMBuilder(1, self.WORD_DIM, self.LSTM_DIM, model),
            LSTMBuilder(1, self.WORD_DIM, self.LSTM_DIM, model)
        ]

        self.model = model
        self.trainer = AdamTrainer(model)
        self.activation = tanh

    def _build_sentence_expressions(self, sentence):
        lstm_forward = self.builders[0].initial_state()
        lstm_backward = self.builders[1].initial_state()

        embeddings_forward = []
        embeddings_backward = []
        for word, reverse_word in zip(sentence, reversed(sentence)):
            lstm_forward = lstm_forward.add_input(word.vector)
            lstm_backward = lstm_backward.add_input(reverse_word.vector)

            embeddings_forward.append(lstm_forward.output())
            embeddings_backward.append(lstm_backward.output())

        H = parameter(self.param_hidden)
        O = parameter(self.param_out)

        sentence_expressions = []
        for word_f_embedding, word_b_embedding in zip(embeddings_forward, reversed(embeddings_backward)):
            word_concat_embedding = concatenate([word_f_embedding, word_b_embedding])
            word_expression = O * self.activation(H * word_concat_embedding)
            sentence_expressions.append(word_expression)
        return sentence_expressions

    def calc_sentence_error(self, sentence):
        renew_cg()

        for word in sentence:
            # word.vector = noise(self._get_word_vector(word), 0.1)

            should_drop = random.random() < 0.3
            word.vector = self._get_word_vector(word) if not should_drop else self._get_word_vector(None)
        sentence_expressions = self._build_sentence_expressions(sentence)

        sentence_errors = []
        for word, word_expression in zip(sentence, sentence_expressions):
            gold_label_index = self.tag_indexer.get_index(word.gold_label)
            word_error = pickneglogsoftmax(word_expression, gold_label_index)
            sentence_errors.append(word_error)
        return esum(sentence_errors)

    def tag_sentence(self, sentence):
        renew_cg()

        for word in sentence:
            word.vector = self._get_word_vector(word)

        sentence_expressions = self._build_sentence_expressions(sentence)
        for word, word_expression in zip(sentence, sentence_expressions):
            out = softmax(word_expression)
            tag_index = np.argmax(out.npvalue())
            word.tag = self.tag_indexer.get_object(tag_index)

    def train(self, train_sentence_list, dev_sentence_list=None, iterations=5):
        train_sentence_list = list(train_sentence_list)

        loss = tagged = 0
        for iteration_idx in xrange(1, iterations+1):
            print "Starting training iteration %d" % iteration_idx
            random.shuffle(train_sentence_list)
            for sentence_index, sentence in enumerate(train_sentence_list, 1):
                sentence_error = self.calc_sentence_error(sentence)
                loss += sentence_error.scalar_value()
                tagged += len(sentence)
                sentence_error.backward()
                self.trainer.update()

            # Trainer Status
            self.trainer.status()
            print loss / tagged
            loss = tagged = 0

            if dev_sentence_list:
                # Dev Evaluation
                for dev_sentence in dev_sentence_list:
                    self.tag_sentence(dev_sentence)
                eval_ner(dev_sentence_list)

    def _get_word_vector(self, word):
        if word is None:
            word_index = self._unk_word_index
        else:
            word_index = self.word_indexer.get_index(word.text) or self._unk_word_index
        return lookup(self.model["word_lookup"], word_index)


def eval_ner(test_sentence_list):
    test_words = []
    for sentence in test_sentence_list:
        test_words.extend(sentence)

    with NamedTemporaryFile(mode='wb') as temp_file:
        format_words(temp_file, test_words, tag_scheme=TAG_SCHEME)
        temp_file.flush()
        os.system(EVAL_NER_CMD.format(test_file=temp_file.name))


def main():
    train_words = parse_words(open(TRAIN_FILE_PATH, 'rb'), tag_scheme=TAG_SCHEME)
    train_sentences = split_words_to_sentences(train_words)
    dev_words = parse_words(open(DEV_FILE_PATH, 'rb'), tag_scheme=TAG_SCHEME)
    dev_sentences = split_words_to_sentences(dev_words)
    test_words = parse_words(open(TEST_FILE_PATH, 'rb'), tag_scheme=TAG_SCHEME)

    external_word_embeddings = {}
    for line in open('/Users/konix/Documents/pos_data/glove.6B/glove.6B.300d.txt', 'rb').readlines():
        word, embedding_str = line.split(' ', 1)
        embedding = np.asarray([float(value_str) for value_str in embedding_str.split()])
        external_word_embeddings[word] = embedding

    word_list = []
    tag_list = []
    for sentence_ in train_sentences:
        for word_ in sentence_:
            word_list.append(word_.text)
            tag_list.append(word_.gold_label)

    word_counter = Counter(word_list)
    word_indexer = Indexer()
    word_indexer.index_object_list(
        [word_text for (word_text, word_count) in word_counter.iteritems() if word_count >= 1]
    )
    word_indexer.index_object_list(external_word_embeddings.keys())
    word_indexer.index_object('_UNK_')

    tag_counter = Counter(tag_list)
    tag_indexer = Indexer()
    tag_indexer.index_object_list(tag_counter.keys())

    tagger = BiLstmNerTagger(word_indexer, tag_indexer, external_word_embeddings)

    del external_word_embeddings
    gc.collect()

    tagger.train(train_sentences, dev_sentences)

    word_index = 0
    while word_index < len(dev_words):
        sentence = dev_words[word_index].sentence
        tagger.tag_sentence(sentence)
        word_index += len(sentence)
    format_words(open('/tmp/dev_ner', 'wb'), dev_words, tag_scheme=TAG_SCHEME)

    word_index = 0
    while word_index < len(test_words):
        sentence = test_words[word_index].sentence
        tagger.tag_sentence(sentence)
        word_index += len(sentence)
    format_words(open('/tmp/test_ner', 'wb'), test_words, tag_scheme=TAG_SCHEME)


if __name__ == '__main__':
    main()
