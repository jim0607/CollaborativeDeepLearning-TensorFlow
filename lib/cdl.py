import numpy as np
import lib.utils as utils
import tensorflow as tf
import sys
import math
import scipy
import scipy.io
import logging

class Params:
    """Parameters for DMF
    """
    def __init__(self):
        self.a = 1
        self.b = 0.01
        self.lambda_u = 0.1
        self.lambda_v = 10
        self.lambda_r = 1
        self.max_iter = 10
        self.M = 300

        # for updating W and b
        self.lr = 0.001
        self.batch_size = 128
        self.n_epochs = 10

class CDL:
    def __init__(self, num_users, num_items, num_factors, params, input_dim, 
        dims, activations, n_z=50, loss_type='cross-entropy', lr=0.1, 
        wd=1e-4, random_seed=0, print_step=50, noise='mask-0.3', dropout=0.0, verbose=True):
        self.m_num_users = num_users
        self.m_num_items = num_items
        self.m_num_factors = num_factors
        self.noise = noise
        self.dropout = dropout # dropout rate

        self.m_U = 0.1 * np.random.randn(self.m_num_users, self.m_num_factors)
        self.m_V = 0.1 * np.random.randn(self.m_num_items, self.m_num_factors)
        self.m_theta = 0.1 * np.random.randn(self.m_num_items, self.m_num_factors)

        self.input_dim = input_dim
        self.dims = dims
        self.activations = activations
        self.lr = lr
        self.params = params
        self.print_step = print_step
        self.verbose = verbose
        self.loss_type = loss_type
        self.n_z = n_z
        self.weights = []
        self.reg_loss = 0

        self.x = tf.placeholder(tf.float32, [None, self.input_dim], name='x')
        self.x_ = tf.placeholder(tf.float32, [None, self.input_dim], name='x_') # added in 2019
        self.v = tf.placeholder(tf.float32, [None, self.m_num_factors])
        self.dropout_prob = tf.placeholder(dtype=tf.float32, name='dropout_probability')

        x_recon = self.inference_generation(self.x)

        # loss
        # reconstruction loss
        if loss_type == 'rmse':
            self.gen_loss = tf.reduce_mean(tf.square(tf.sub(self.x, x_recon)))
        elif loss_type == 'cross-entropy':
            x_recon = tf.nn.sigmoid(x_recon, name='x_recon')
            self.gen_loss = -tf.reduce_mean(tf.reduce_sum(self.x_ * tf.log(tf.maximum(x_recon, 1e-10)) 
                + (1-self.x_) * tf.log(tf.maximum(1 - x_recon, 1e-10)),1))

        self.v_loss = 1.0*params.lambda_v/params.lambda_r * tf.reduce_mean( tf.reduce_sum(tf.square(self.v - self.z), 1))

        self.loss = self.gen_loss + self.v_loss + 2e-4*self.reg_loss
        self.optimizer = tf.train.AdamOptimizer(self.lr).minimize(self.loss)

        # Initializing the tensor flow variables
        self.saver = tf.train.Saver(self.weights)
        init = tf.global_variables_initializer()

        # Launch the session
        self.sess = tf.Session()
        self.sess.run(init)

    def add_noise(self, x):
        if self.noise == 'gaussian':
            n = np.random.normal(0, 0.1, (len(x), len(x[0])))
            return x + n
        if 'mask' in self.noise:
            frac = float(self.noise.split('-')[1])
            temp = np.copy(x)
            for i in temp:
                n = np.random.choice(len(i), int(round(
                    frac * len(i))), replace=False)
                i[n] = 0
            return temp
        if self.noise == 'sp':
            return x
            #pass

    def inference_generation(self, x):
        with tf.variable_scope("inference"):
            rec = {'W1': tf.get_variable("W1", [self.input_dim, self.dims[0]], 
                    initializer=tf.contrib.layers.xavier_initializer(), dtype=tf.float32),
                'b1': tf.get_variable("b1", [self.dims[0]], 
                    initializer=tf.constant_initializer(0.0), dtype=tf.float32),
                'W_z_mean': tf.get_variable("W_z_mean", [self.dims[0], self.n_z], 
                    initializer=tf.contrib.layers.xavier_initializer(), dtype=tf.float32),
                'b_z_mean': tf.get_variable("b_z_mean", [self.n_z], 
                    initializer=tf.constant_initializer(0.0), dtype=tf.float32)}

        self.weights += [rec['W1'], rec['b1'], rec['W_z_mean'], rec['b_z_mean']]
        self.reg_loss += tf.nn.l2_loss(rec['W1'])# + tf.nn.l2_loss(rec['W2'])
        h1 = self.activate(
            tf.matmul(x, rec['W1']) + rec['b1'], self.activations[0])
        dropout_h1 = tf.nn.dropout(h1, self.dropout_prob)
        self.z_mean = tf.matmul(dropout_h1, rec['W_z_mean']) + rec['b_z_mean']

        self.z = self.z_mean
        self.dropout_z = tf.nn.dropout(self.z, self.dropout_prob)


        with tf.variable_scope("generation"):
            gen = {'W1': tf.get_variable("W1", [self.n_z, self.dims[0]], 
                    initializer=tf.contrib.layers.xavier_initializer(), dtype=tf.float32),
                'b1': tf.get_variable("b1", [self.dims[0]], 
                    initializer=tf.constant_initializer(0.0), dtype=tf.float32),
                'W_x': tf.transpose(rec['W1']),
                'b_x': tf.get_variable("b_x", [self.input_dim], 
                    initializer=tf.constant_initializer(0.0), dtype=tf.float32)}

        self.weights += [gen['W1'], gen['b1'], gen['b_x']]
        self.reg_loss += tf.nn.l2_loss(gen['W1']) + tf.nn.l2_loss(gen['W_x'])
        h1 = self.activate(
            tf.matmul(self.dropout_z, gen['W1']) + gen['b1'], self.activations[0])
        dropout_h1 = tf.nn.dropout(h1, self.dropout_prob)
        x_recon = tf.matmul(dropout_h1, gen['W_x']) + gen['b_x']

        return x_recon

    def cdl_estimate(self, data_x, num_iter):
        for i in range(num_iter):
            b_x_, ids = utils.get_batch(data_x, self.params.batch_size)
            b_x = self.add_noise(b_x_) # denoising
            _, l, gen_loss, v_loss = self.sess.run((self.optimizer, self.loss, self.gen_loss, self.v_loss), feed_dict={self.x: b_x, self.x_: b_x_, self.v: self.m_V[ids, :], self.dropout_prob: self.dropout})
            # Display logs per epoch step
            if i % self.print_step == 0 and self.verbose:
                print ("Iter:", '%04d' % (i+1), \
                      "loss=", "{:.5f}".format(l), \
                      "genloss=", "{:.5f}".format(gen_loss), \
                      "vloss=", "{:.5f}".format(v_loss))
        return gen_loss

    def transform(self, data_x):
        data_en = self.sess.run(self.z_mean, feed_dict={self.x: data_x, self.dropout_prob: 1.0})
        return data_en

    def pmf_estimate(self, users, items, test_users, test_items, params, epoch):
        """
        users: list of list
        """
        min_iter = 1
        max_iter = params.max_iter if epoch == params.n_epochs-1 else 1
        a_minus_b = params.a - params.b
        converge = 1.0
        likelihood_old = 0.0
        likelihood = -math.exp(20)
        it = 0
        while ((it < max_iter and converge > 1e-6) or it < min_iter):
            likelihood_old = likelihood
            likelihood = 0
            # update U
            # VV^T for v_j that has at least one user liked
            ids = np.array([len(x) for x in items]) > 0
            v = self.m_V[ids]
            VVT = np.dot(v.T, v)
            XX = VVT * params.b + np.eye(self.m_num_factors) * params.lambda_u

            #for i in xrange(self.m_num_users):
            for i in range(self.m_num_users):
                item_ids = users[i]
                n = len(item_ids)
                if n > 0:
                    A = np.copy(XX)
                    A += np.dot(self.m_V[item_ids, :].T, self.m_V[item_ids,:])*a_minus_b
                    x = params.a * np.sum(self.m_V[item_ids, :], axis=0)
                    self.m_U[i, :] = scipy.linalg.solve(A, x)
                    
                    likelihood += -0.5 * params.lambda_u * np.sum(self.m_U[i]*self.m_U[i])

            # update V
            ids = np.array([len(x) for x in users]) > 0
            u = self.m_U[ids]
            XX = np.dot(u.T, u) * params.b
            for j in range(self.m_num_items):
                user_ids = items[j]
                m = len(user_ids)
                if m>0 :
                    A = np.copy(XX)
                    A += np.dot(self.m_U[user_ids,:].T, self.m_U[user_ids,:])*a_minus_b
                    B = np.copy(A)
                    A += np.eye(self.m_num_factors) * params.lambda_v
                    x = params.a * np.sum(self.m_U[user_ids, :], axis=0) + params.lambda_v * self.m_theta[j,:]
                    self.m_V[j, :] = scipy.linalg.solve(A, x)
                    
                    likelihood += -0.5 * m * params.a
                    likelihood += params.a * np.sum(np.dot(self.m_U[user_ids, :], self.m_V[j,:][:, np.newaxis]),axis=0)
                    likelihood += -0.5 * self.m_V[j,:].dot(B).dot(self.m_V[j,:][:,np.newaxis])

                    ep = self.m_V[j,:] - self.m_theta[j,:]
                    likelihood += -0.5 * params.lambda_v * np.sum(ep*ep) 
                else:
                    # m=0, this article has never been rated
                    A = np.copy(XX)
                    A += np.eye(self.m_num_factors) * params.lambda_v
                    x = params.lambda_v * self.m_theta[j,:]
                    self.m_V[j, :] = scipy.linalg.solve(A, x)
                    
                    ep = self.m_V[j,:] - self.m_theta[j,:]
                    likelihood += -0.5 * params.lambda_v * np.sum(ep*ep)
            
            it += 1
            converge = abs(1.0*(likelihood - likelihood_old)/likelihood_old)

            if self.verbose:
                if likelihood < likelihood_old:
                    print("likelihood is decreasing!")

                print("[iter=%04d], likelihood=%.5f, converge=%.10f" % (it, likelihood, converge))

        return likelihood

    def activate(self, linear, name):
        if name == 'sigmoid':
            return tf.nn.sigmoid(linear, name='encoded')
        elif name == 'softmax':
            return tf.nn.softmax(linear, name='encoded')
        elif name == 'linear':
            return linear
        elif name == 'tanh':
            return tf.nn.tanh(linear, name='encoded')
        elif name == 'relu':
            return tf.nn.relu(linear, name='encoded')

    def run(self, users, items, test_users, test_items, data_x, params):
        self.m_theta[:] = self.transform(data_x)
        self.m_V[:] = self.m_theta
        n = data_x.shape[0]
        for epoch in range(params.n_epochs):
            num_iter = int(n / params.batch_size)
            gen_loss = self.cdl_estimate(data_x, num_iter)
            self.m_theta[:] = self.transform(data_x)
            likelihood = self.pmf_estimate(users, items, test_users, test_items, params, epoch)
            loss = -likelihood + 0.5 * gen_loss * n * params.lambda_r
            logging.info("[#epoch=%06d], loss=%.5f, neg_likelihood=%.5f, gen_loss=%.5f" % (
                epoch, loss, -likelihood, gen_loss))

    def save_model(self, weight_path, pmf_path=None):
        self.saver.save(self.sess, weight_path)
        logging.info("Weights saved at " + weight_path)
        if pmf_path is not None:
            scipy.io.savemat(pmf_path,{"m_U": self.m_U, "m_V": self.m_V, "m_theta": self.m_theta})
            logging.info("Weights saved at " + pmf_path)

    def load_model(self, weight_path, pmf_path=None):
        logging.info("Loading weights from " + weight_path)
        self.saver.restore(self.sess, weight_path)
        if pmf_path is not None:
            logging.info("Loading pmf data from " + pmf_path)
            data = scipy.io.loadmat(pmf_path)
            self.m_U[:] = data["m_U"]
            self.m_V[:] = data["m_V"]
            self.m_theta[:] = data["m_theta"]

