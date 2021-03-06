from __future__ import print_function, division
from signalmaps_helper import *
import math
import tensorflow as tf
from tensorflow import keras
from keras.layers import Input, Dense, Reshape, Flatten, Dropout
from keras.layers import BatchNormalization, Activation, ZeroPadding2D
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.convolutional import UpSampling2D, Conv2D
from keras.models import Sequential, Model
from keras.optimizers import Adam
from keras.utils import to_categorical
import matplotlib.pyplot as plt
import sys
import numpy as np

class NOISE_PRIVATIZER():

    def __init__(self, all_data, norm_all_data, num_users, batch_size, epsilon, delta, norm_clip):
        self.num_users = num_users
        self.epsilon = epsilon
        self.delta = delta
        self.norm_clip = norm_clip
        self.batch_size = batch_size
        self.all_data = all_data
        self.norm_all_data = norm_all_data
        self.n = norm_all_data.shape[0]
        self.input_shape = (batch_size, norm_all_data.shape[1]-1)
        self.x1min, self.x1max, self.x2min, self.x2max = np.min(norm_all_data[:,13]), np.max(norm_all_data[:,13]), np.min(norm_all_data[:,12]), np.max(norm_all_data[:,12])
        # TODO experiment with these
        optimizer = Adam(lr=0.001, beta_1=0.9)
        self.adversary = self.build_adversary()
        self.adversary.compile(loss=self.adversary_loss,
            optimizer=optimizer,
            metrics=['accuracy'])
        self.privatizer = self.build_privatizer()

    def adversary_loss(self, u, uhat):
        return keras.losses.categorical_crossentropy(u, uhat)

    def build_privatizer(self):
        # if unconcerned with DP guarantees, set sigma directly
        sigma = (self.norm_clip/self.epsilon)*math.sqrt(2*math.log(1.25/self.delta))
        # sigma = 20.0
        def priv(x):
            noise = np.random.normal(loc=0.0, scale=sigma, size=(self.n, 25))
            y = x + noise
            return y
        return priv

    def build_adversary(self):
        model = Sequential()
        model.add(Dense(32, input_dim=self.input_shape[1]))
        model.add(LeakyReLU(alpha=0.2))
        model.add(BatchNormalization(momentum=0.8))
        model.add(Dense(32))
        model.add(LeakyReLU(alpha=0.2))
        model.add(BatchNormalization(momentum=0.8))
        model.add(Dense(self.num_users, activation='softmax'))
        y = Input(shape=(None, self.input_shape[1]))
        uhat = model(y)
        return Model(y, uhat)

    def privatize(self):
        X = self.norm_all_data[:,:25]
        if self.norm_clip is None:
            self.X = X
        else:
            normvec = np.linalg.norm(X, axis=1)
            scalevec = self.norm_clip/normvec
            scalevec[np.where(scalevec>1)] = 1
            self.X = (X.T*scalevec).T
        self.Y = self.privatizer(self.X)
        self.u = to_categorical(self.all_data[:,25])

    def eval_utility(self):
        v = vandermonde(self.X, 2)
        b = beta(v, self.X[:,6])
        v_obf = vandermonde(self.Y, 2)
        b_obf = beta(v_obf,self.Y[:,6])
        beta_loss = np.mean(np.square(b-b_obf))
        print("UTILITY LOSS 1 (error in map fitting):", beta_loss)

        distortion_loss = np.mean(np.square(self.X-self.Y))
        print("UTILITY LOSS 2 (full dataset distance):", distortion_loss)

        geographic_distortion = np.mean(np.square(self.X[:,12:14]-self.Y[:,12:14]))
        print("UTILITY LOSS 3 (just geographic distance):", geographic_distortion)

        num_grids = 15
        true_count_per_grid = np.zeros(shape=(num_grids, num_grids))
        obf_count_per_grid = np.zeros(shape=(num_grids, num_grids))
        size1 = self.x1max-self.x1min
        size2 = self.x2max-self.x2min
        for i in range(num_grids):
            for j in range(num_grids):
                a = self.x1min+(size1/num_grids*i)
                b = self.x1min+(size1/num_grids*(i+1))
                c = self.x2min+(size2/num_grids*j)
                d = self.x2min+(size2/num_grids*(j+1))
                true_count_per_grid[i][j] = self.X[(self.X[:,13] >= a ) & (self.X[:,13] < b) & (self.X[:,12] >= c) & (self.X[:,12] < d)].shape[0]
                obf_count_per_grid[i][j] = self.Y[(self.Y[:,13] >= a ) & (self.Y[:,13] < b) & (self.Y[:,12] >= c) & (self.Y[:,12] < d)].shape[0]
        geographic_density = np.mean(np.square(true_count_per_grid-obf_count_per_grid))
        print("UTILITY LOSS 4 (geographic density):", geographic_density)

    def split_data(self, train_portion):
        idx = np.arange(self.n)
        np.random.shuffle(idx)
        k = int(train_portion*self.n)
        trainidx = idx[:k]
        testidx = idx[k:]
        self.Y_train = self.Y[trainidx]
        self.Y_test = self.Y[testidx]
        self.u_train = self.u[trainidx]
        self.u_test = self.u[testidx]
        extra_epochs = 3000
        self.adversary_epochs = int(k/self.batch_size)+extra_epochs

    def train(self, seed=False):
        print("training for", self.adversary_epochs, "epochs")
        for epoch in range(self.adversary_epochs):
            # Select a random batch
            if seed:
                np.random.seed(0)
            idx = np.random.randint(0, self.Y_train.shape[0], self.batch_size)
            Y_batch = self.Y_train[idx].reshape(1, self.batch_size, 25)
            u_batch = self.u_train[idx].reshape(1, self.batch_size, self.num_users)
            # Train the adversary
            a_loss = self.adversary.train_on_batch(Y_batch, u_batch)
            # log the progress
            if epoch % 100 == 0:
                print ("%d [A loss: %f, acc.: %.2f%%]" % (epoch, a_loss[0], 100*a_loss[1]))

    def eval_privacy(self):
        Y = self.Y_test
        u = self.u_test
        a_loss = self.adversary.evaluate(Y.reshape(1, Y.shape[0], Y.shape[1]), u.reshape(1, u.shape[0], u.shape[1]), verbose=0)
        print("PRIVACY LOSS:", a_loss[0])

    def showplots(self):
        X = self.X[0:100]
        Y = self.Y[0:100]
        fig, ax = plt.subplots(1, 2, figsize=(15, 5))
        ax[0].scatter(X[:,13], X[:,12], c=X[:,6].tolist())
        ax[0].set_title("Input Data")
        ax[1].scatter(Y[:,13], Y[:,12], c=Y[:,6].tolist())
        ax[1].set_title("Obfuscated Data")
        plt.show()

all_data = np.genfromtxt('augmented_data')
norm_all_data = np.genfromtxt('normalized_augmented_data')

n = NOISE_PRIVATIZER(all_data, norm_all_data, num_users=9, batch_size=512, epsilon=0.05, delta=10**-5, norm_clip=4.0)
n.privatize()
n.eval_utility()
n.split_data(train_portion = 0.8)
n.train()
n.eval_privacy()
# n.showplots()
