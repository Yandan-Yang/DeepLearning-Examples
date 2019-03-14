import numpy as np
from keras.models import Sequential
from keras.layers import Dense
from keras.layers import LSTM
from keras.layers import Activation
from keras.layers import Masking
from keras.optimizers import RMSprop
from keras import backend as k
from sklearn.preprocessing import normalize
from keras.models import model_from_json
import matplotlib.pyplot as plt


"""
    Discrete log-likelihood for Weibull hazard function on censored survival data
    y_true is a (samples, 2) tensor containing time-to-event (y), and an event indicator (u)
    ab_pred is a (samples, 2) tensor containing predicted Weibull alpha (a) and beta (b) parameters
    For math, see https://ragulpr.github.io/assets/draft_master_thesis_martinsson_egil_wtte_rnn_2016.pdf (Page 35)
"""
def weibull_loglik_discrete(y_true, ab_pred, name=None):
    y_ = y_true[:, 0]
    u_ = y_true[:, 1]
    a_ = ab_pred[:, 0]
    b_ = ab_pred[:, 1]

    hazard0 = k.pow((y_ + 1e-35) / a_, b_)
    hazard1 = k.pow((y_ + 1) / a_, b_)

    return -1 * k.mean(u_ * k.log(k.exp(hazard1 - hazard0) - 1.0) - hazard1)

"""
    Not used for this model, but included in case somebody needs it
    For math, see https://ragulpr.github.io/assets/draft_master_thesis_martinsson_egil_wtte_rnn_2016.pdf (Page 35)
"""
def weibull_loglik_continuous(y_true, ab_pred, name=None):
    y_ = y_true[:, 0]
    u_ = y_true[:, 1]
    a_ = ab_pred[:, 0]
    b_ = ab_pred[:, 1]

    ya = (y_ + 1e-35) / a_
    return -1 * k.mean(u_ * (k.log(b_) + b_ * k.log(ya)) - k.pow(ya, b_))


"""
    Custom Keras activation function, outputs alpha neuron using exponentiation and beta using softplus
"""
def activate(ab):
    a = k.exp(ab[:, 0])
    b = k.softplus(ab[:, 1])

    a = k.reshape(a, (k.shape(a)[0], 1))
    b = k.reshape(b, (k.shape(b)[0], 1))

    return k.concatenate((a, b), axis=1)


"""
    Load and parse engine data files into:
       - an (engine/day, observed history, sensor readings) x tensor, where observed history is 100 days, zero-padded
         for days that don't have a full 100 days of observed history (e.g., first observed day for an engine)
       - an (engine/day, 2) tensor containing time-to-event and 1 (since all engines failed)

    There are probably MUCH better ways of doing this, but I don't use Numpy that much, and the data parsing isn't the
    point of this demo anyway.
"""
def load_file(name):
    with open(name, 'r') as file:
        return np.loadtxt(file, delimiter=',')

np.set_printoptions(suppress=True, threshold=10000)

train1 = load_file('train.csv')
test1_x = load_file('test_x.csv')
test1_y = load_file('test_y.csv')

# Combine the X values to normalize them, then split them back out
all_x = np.concatenate((train1[:, 2:26], test1_x[:, 2:26]))
all_x = normalize(all_x, axis=0)

train1[:, 2:26] = all_x[0:train1.shape[0], :]
test1_x[:, 2:26] = all_x[train1.shape[0]:, :]

# Make engine numbers and days zero-indexed, for everybody's sanity
train1[:, 0:2] -= 1
test1_x[:, 0:2] -= 1

# Configurable observation look-back period for each engine/day
max_time = 100

def build_data(engine, time, x, max_time, is_test):
    # y[0] will be days remaining, y[1] will be event indicator, always 1 for this data
    out_y = np.empty((0, 2), dtype=np.float32)

    # A full history of sensor readings to date for each x
    out_x = np.empty((0, max_time, 24), dtype=np.float32)

    for i in range(100):
        print("Engine: " + str(i))
        # When did the engine fail? (Last day + 1 for train data, irrelevant for test.)
        max_engine_time = int(np.max(time[engine == i])) + 1

        if is_test:
            start = max_engine_time - 1
        else:
            start = 0

        this_x = np.empty((0, max_time, 24), dtype=np.float32)

        for j in range(start, max_engine_time):
            engine_x = x[engine == i]

            out_y = np.append(out_y, np.array((max_engine_time - j, 1), ndmin=2), axis=0)

            xtemp = np.zeros((1, max_time, 24))
            xtemp[:, max_time-min(j, 99)-1:max_time, :] = engine_x[max(0, j-max_time+1):j+1, :]
            this_x = np.concatenate((this_x, xtemp))

        out_x = np.concatenate((out_x, this_x))

    return out_x, out_y

train1_x, train1_y = build_data(train1[:, 0], train1[:, 1], train1[:, 2:26], max_time, False)
test1_x = build_data(test1_x[:, 0], test1_x[:, 1], test1_x[:, 2:26], max_time, True)[0]

train_u = np.zeros((100, 1), dtype=np.float32)
train_u += 1
test1_y = np.append(np.reshape(test1_y, (100, 1)), train_u, axis=1)

"""
    Here's the rest of the meat of the demo... actually fitting and training the model.
    We'll also make some test predictions so we can evaluate model performance.
"""

# Start building our model
model = Sequential()

# Mask parts of the lookback period that are all zeros (i.e., unobserved) so they don't skew the model
model.add(Masking(mask_value=0., input_shape=(max_time, 24)))

# LSTM is just a common type of RNN. You could also try anything else (e.g., GRU).
model.add(LSTM(20, input_dim=24))

# We need 2 neurons to output Alpha and Beta parameters for our Weibull distribution
model.add(Dense(2))

# Apply the custom activation function mentioned above
model.add(Activation(activate))

# Use the discrete log-likelihood for Weibull survival data as our loss function
model.compile(loss=weibull_loglik_discrete, optimizer=RMSprop(lr=.001))

# Fit!
model.fit(train1_x, train1_y, nb_epoch=100, batch_size=2000, verbose=2, validation_data=(test1_x, test1_y))

# Make some predictions and put them alongside the real TTE and event indicator values
test_predict = model.predict(test1_x)
test_predict = np.resize(test_predict, (100, 2))
test_result = np.concatenate((test1_y, test_predict), axis=1)

# TTE, Event Indicator, Alpha, Beta
print(test_result)

#point estimate of the remaining life
#survial function: exp(-(t/a)^b). Suppose the threshold of the survive is set as 0.5, then solve equation
#we have the point estimate of  a(-ln(.5))^(1/b)
TTE_predict_point = test_predict[:,0]*(-np.log(.5))**np.exp(1/test_predict[:,1])
plt.scatter( test1_y[:, 0], TTE_predict_point)
plt.xlabel('predicted churn date')
plt.ylabel('true churn date')

plt.scatter(test_predict[:, 0], test_predict[:, 1])

plt.xlabel('predicted churn date')
plt.ylabel('true churn date')


################################################################################

from keras.models import Sequential
from keras.layers import Dense
from keras.models import model_from_json
# serialize model to JSON
model_json = model.to_json()
with open("model.json", "w") as json_file:
    json_file.write(model_json)
# serialize weights to HDF5
model.save_weights("model.h5")
print("Saved model to disk")



# load json and create model
json_file = open('model.json', 'r')
loaded_model_json = json_file.read()
json_file.close()
loaded_model = model_from_json(loaded_model_json)
# load weights into new model
loaded_model.load_weights("model.h5")
print("Loaded model from disk")