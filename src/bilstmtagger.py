from pycnn import *
from collections import Counter
import random
import gc
import numpy as np

import util

# format of files: each line is "word<TAB>tag<newline>", blank line is new sentence.
train_file="/Users/konix/Documents/pos_data/format_train"
test_file="/Users/konix/Documents/pos_data/format_test"


MLP=True

def read(fname):
    sent = []
    for line in file(fname):
        line = line.strip().split()
        if not line:
            if sent: yield sent
            sent = []
        else:
            w,p = line
            sent.append((w,p))

train=list(read(train_file))
test=list(read(test_file))
words=[]
tags=[]
wc=Counter()
for s in train:
    for w,p in s:
        words.append(w)
        tags.append(p)
        wc[w]+=1
words.append("_UNK_")
#words=[w if wc[w] > 1 else "_UNK_" for w in words]
tags.append("_START_")

for s in test:
    for w,p in s:
        words.append(w)

vw = util.Vocab.from_corpus([words])
vt = util.Vocab.from_corpus([tags])
UNK = vw.w2i["_UNK_"]

nwords = vw.size()
ntags  = vt.size()

model = Model()
sgd = SimpleSGDTrainer(model)

model.add_lookup_parameters("lookup", (nwords, 200))
model.add_lookup_parameters("tl", (ntags, 30))

# My Code: initialize word lookup based on pre-trained embeddings
embedding_by_word = {}
for line in open('/Users/konix/Documents/pos_data/glove.6B/glove.6B.200d.txt', 'rb').readlines():
    word, embedding_str = line.split(' ', 1)
    embedding = np.asarray([float(value_str) for value_str in embedding_str.split()])
    embedding_by_word[word] = embedding
word_lookup = model["lookup"]
for idx in xrange(vw.size()):
    word = vw.i2w[idx]
    if word in embedding_by_word:
        word_lookup.init_row(idx, embedding_by_word[word])
del embedding_by_word
gc.collect()

if MLP:
    pH = model.add_parameters("HID", (32, 50*2))
    pO = model.add_parameters("OUT", (ntags, 32))
else:
    pO = model.add_parameters("OUT", (ntags, 50*2))

builders=[
        LSTMBuilder(1, 200, 50, model),
        LSTMBuilder(1, 200, 50, model),
        ]

def build_tagging_graph(words, tags, model, builders):
    renew_cg()
    f_init, b_init = [b.initial_state() for b in builders]

    wembs = [lookup(model["lookup"], w) for w in words]
    wembs = [noise(we,0.1) for we in wembs]

    fw = [x.output() for x in f_init.add_inputs(wembs)]
    bw = [x.output() for x in b_init.add_inputs(reversed(wembs))]

    if MLP:
        H = parameter(pH)
        O = parameter(pO)
    else:
        O = parameter(pO)
    errs = []
    for f,b,t in zip(fw, reversed(bw), tags):
        f_b = concatenate([f,b])
        if MLP:
            r_t = O*(tanh(H * f_b))
        else:
            r_t = O * f_b
        err = pickneglogsoftmax(r_t, t)
        errs.append(err)
    return esum(errs)

def tag_sent(sent, model, builders):
    renew_cg()
    f_init, b_init = [b.initial_state() for b in builders]
    wembs = [lookup(model["lookup"], vw.w2i.get(w, UNK)) for w,t in sent]

    fw = [x.output() for x in f_init.add_inputs(wembs)]
    bw = [x.output() for x in b_init.add_inputs(reversed(wembs))]

    if MLP:
        H = parameter(pH)
        O = parameter(pO)
    else:
        O = parameter(pO)
    tags=[]
    for f,b,(w,t) in zip(fw,reversed(bw),sent):
        if MLP:
            r_t = O*(tanh(H * concatenate([f,b])))
        else:
            r_t = O*concatenate([f,b])
        out = softmax(r_t)
        chosen = np.argmax(out.npvalue())
        tags.append(vt.i2w[chosen])
    return tags


tagged = loss = 0
for ITER in xrange(50):
    random.shuffle(train)
    for i,s in enumerate(train,1):
        if i % 5000 == 0:
            sgd.status()
            print loss / tagged
            loss = 0
            tagged = 0
        if i % 10000 == 0:
            good = bad = 0.0
            for sent in test:
                tags = tag_sent(sent, model, builders)
                golds = [t for w,t in sent]
                for go,gu in zip(golds,tags):
                    if go == gu: good +=1 
                    else: bad+=1
            print good/(good+bad)
        ws = [vw.w2i.get(w, UNK) for w,p in s]
        ps = [vt.w2i[p] for w,p in s]
        sum_errs = build_tagging_graph(ws,ps,model,builders)
        squared = -sum_errs# * sum_errs
        loss += sum_errs.scalar_value()
        tagged += len(ps)
        sum_errs.backward()
        sgd.update()


