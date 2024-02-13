#This code is for 1-D burger equation.

#from IPython import get_ipython
#get_ipython().magic('reset -sf')

# %%
import numpy as np
import torch
import scipy.io
import matplotlib.pyplot as plt
from module_wno_2d import *
from timeit import default_timer
from utilities3 import *
import gradfree_fun 

torch.manual_seed(0)
np.random.seed(0)

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
# device = torch.device('cpu')

# %%
""" Model configurations """

reader = scipy.io.loadmat('Data/u_sol2_burger.mat')  	# Load data from file
x = torch.tensor(reader['x'],dtype = torch.float)                                   # 200 points between -1 and 1 [256x1]
y = torch.tensor(reader['t'],dtype = torch.float)                                   # 200 time points between 0 and 1 [100x1] 
usol = torch.tensor(reader['sol'],dtype = torch.float) 

x_train1 = torch.tensor(reader['mat_ics'],dtype = torch.float) 
y_train1 = usol 

ntrain = 300
ntrain1 = 0
ntest = 50

batch_size = 25
learning_rate = 0.01
epochs = 10
step_size = 20
gamma = 0.5

level = 4
width = 32

r = 3
h = int(((241 - 1)/r) + 1)
s = h

# %%
""" Read data """

x_train = x_train1[:ntrain,::r,::r][:,:s,:s]
y_train = y_train1[:ntrain,::r,::r][:,:s,:s]

x_test = x_train1[ntrain:ntrain+ntest,::r,::r][:,:s,:s]
y_test = y_train1[ntrain:ntrain+ntest,::r,::r][:,:s,:s]

x_normalizer = UnitGaussianNormalizer(x_train)
x_train = x_normalizer.encode(x_train)
x_test = x_normalizer.encode(x_test)

y_normalizer = UnitGaussianNormalizer(y_train)
y_train = y_normalizer.encode(y_train)

x_train = x_train.reshape(ntrain,s,s,1).float()

x_test = x_test.reshape(ntest,s,s,1).float()

train_loader1 = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_train, y_train),
                                           batch_size=batch_size, shuffle=True)

test_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_test, y_test),
                                          batch_size=batch_size, shuffle=False)

# %%
lb = np.array([0, 0])
ub = np.array([1, 1])
N_f = h
xt1=lb[0] + (ub[0]-lb[0])*np.linspace(0,1,N_f)
yt1=lb[1] + (ub[1]-lb[1])*np.linspace(0,1,N_f)
Xt1, Yt1 = np.meshgrid(xt1,yt1)
X_f_train = np.hstack([Xt1.reshape(N_f*N_f,1),Yt1.reshape(N_f*N_f,1)])
x_f_train = torch.tensor(X_f_train,dtype=torch.float).to(device)

gf = gradfree_fun.gradientfree().cuda() if torch.cuda.is_available() else gradfree_fun.gradientfree().cpu()
p_index = gf.neighbour_index(x_f_train)
invp_index = gf.inverse_index(x_f_train)

# %%
""" The model definition """
model = WNO2d(width, level, x_train[0:1,:,:,:].permute(0,3,1,2)).to(device)
print(count_params(model))

""" Training and testing """
optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate, weight_decay=1e-4)
scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=step_size, gamma=gamma)

train_loss = torch.zeros(epochs)
test_loss = torch.zeros(epochs)
y_normalizer.cuda() if torch.cuda.is_available() else y_normalizer.cpu()
for ep in range(epochs):
    model.train()
    t1 = default_timer()
    train_l2 = 0
    train_dl = 0
    train_pl = 0
    # print(model.mu.gg)
    for x, y in train_loader1:
        x, y = x.to(device), y.to(device)

        physics_loss = 0
        optimizer.zero_grad()
        
        out = model(x).reshape(batch_size, h, s)
        out = y_normalizer.decode(out)
        y = y_normalizer.decode(y)
        
        # data_loss = F.mse_loss(out, y, reduction='mean')
        
        for kk in range(out.shape[0]):
            y_pred = out[kk,:,:]
            y_dash = y[kk,:,:]
            #bound condition 1
            tp_u = y_pred[0,:][:,None]
            tp_usol = y_dash[0,:][:,None]       
            lt_u =  y_pred[:,0][:,None] #L2
            lt_usol = y_dash[:,0][:,None] #L2
            rt_u = y_pred[:,-1][:,None]
            rt_usol = y_dash[:,-1][:,None]
            
            all_u_train = torch.vstack([tp_u,lt_u,rt_u]) # X_u_train [200,2] (800 = 200(L1)+200(L2)+200(L3)+200(L4))
            all_u_sol = torch.vstack([tp_usol,lt_usol,rt_usol])   #corresponding u [800x1]

            y_pf = y_pred.reshape(N_f*N_f,1)
            x_pf = x[kk,:,:,:].reshape(N_f*N_f,1)
            physics_loss += gf.loss(all_u_train,all_u_sol,x_f_train,y_pf,p_index,invp_index)
        
    
        loss =  physics_loss
        loss.backward()
        optimizer.step()
  
    train_pl += physics_loss.item()
    train_l2 = train_pl
    scheduler.step()
    model.eval()
    test_l2 = 0.0
    with torch.no_grad():
        for x, y in test_loader:
            x, y = x.to(device), y.to(device)
            
            out = model(x).reshape(batch_size, s, s)
            out = y_normalizer.decode(out)
            test_l2 += (torch.norm(out.reshape(batch_size, h,s)-y)/torch.norm(y)).item()
            
    train_l2/= ntrain
    test_l2 /= ntest
    
    train_loss[ep] = train_l2
    test_loss[ep] = test_l2
    t2 = default_timer()
    print('Epoch %d - Time %0.4f - Physics %0.4f - Test %0.4f'
          % (ep, t2-t1, train_pl/ntrain, test_l2/ntest))
    
# %%
""" Prediction """
pred = torch.zeros(y_test.shape)
index = 0
test_e = torch.zeros(y_test.shape[0])
test_loader = torch.utils.data.DataLoader(torch.utils.data.TensorDataset(x_test, y_test),
                                          batch_size=1, shuffle=False)
with torch.no_grad():
    for x, y in test_loader:
        test_l2 = 0
        x, y = x.to(device), y.to(device)

        out = model(x).squeeze(0).squeeze(-1)
        out = y_normalizer.decode(out)
        pred[index] = out

        # test_l2 += (torch.norm(out-y.reshape(h,s))/torch.norm(y.reshape(h,s))).item()
        test_l2 += torch.linalg.norm((out-y.reshape(h,s)),2)/torch.linalg.norm(y.reshape(h,s),2)
        
        
        test_e[index] = test_l2
        # print(index, test_l2)
        index = index + 1
        
print('Mean Testing Error:', 100*torch.mean(test_e).numpy(), '%')

# %%
""" Plotting """ # for paper figures please see 'WNO_testing_(.).py' files
figure7 = plt.figure(figsize = (12, 8))
plt.subplots_adjust(wspace=0.2,hspace=0.5)
index = 0
for value in range(y_test.shape[0]):
    if value % 12== 0:
        plt.subplot(3,5, index+1)
        plt.imshow(y_test[value,:,:], cmap='gist_ncar', interpolation='Gaussian')
        plt.title('Actual'); plt.colorbar(fraction=0.045)
        plt.subplot(3,5, index+1+5)
        plt.imshow(pred.cpu().detach()[value,:,:], cmap='gist_ncar', interpolation='Gaussian')
        plt.title('Identified'); plt.colorbar(fraction=0.045)
        plt.subplot(3,5, index+1+10)
        plt.imshow(np.abs(pred.cpu().detach()[value,:,:]-y_test[value,:,:]), cmap='gist_ncar', interpolation='Gaussian')
        plt.title('Identified'); plt.colorbar(fraction=0.045)
        plt.margins(0)
        index = index + 1

# %%
""" For saving the trained model and prediction data """

torch.save(model, 'model/model_phy_cwno_burger')
scipy.io.savemat('pred/prediction_pinn_wno_burger.mat', mdict={'pred': pred.cpu().numpy()})

scipy.io.savemat('epoch_loss/train_loss_burger.mat', mdict={'train_loss': train_loss.cpu().numpy()})
scipy.io.savemat('epoch_loss/pred_loss_burger.mat', mdict= {'test_loss': test_loss.cpu().numpy()})

# %%

