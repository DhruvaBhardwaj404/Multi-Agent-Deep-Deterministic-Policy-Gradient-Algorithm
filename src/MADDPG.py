import pandas as pd
import torch
from agilerl.components.replay_buffer import ReplayBuffer
import numpy
import time
from src.helpers import *
from src.Agent import Agent
from tensordict import TensorDict
from torchrl.data import ReplayBuffer, ListStorage
import warnings


class MADDPG:
    """
    Implements Multi-Agent Deep Deterministic Policy Gradient Algorithm
    """
    def __init__(self,obs_size:int,action_size:int,agent_num:int,gamma:float,
                 eps:float,tau:float,threshold:int,device:str,
                 batch_size = 1024,eps_decay:int=1000):
        """

        @param obs_size:
        @type obs_size:
        @param action_size:
        @type action_size:
        @param agent_num:
        @type agent_num:
        @param gamma:
        @type gamma:
        @param eps:
        @type eps:
        @param tau:
        @type tau:
        @param threshold:
        @type threshold:
        @param device:
        @type device:
        @param eps_decay:
        @type eps_decay:
        """
        torch.autograd.set_detect_anomaly(True)
        warnings.filterwarnings("ignore")
        self.action_size = action_size
        self.obs_size = obs_size
        self.agent_num = agent_num
        self.gamma = torch.tensor(gamma).to(device)
        self.eps = torch.tensor(eps).to(device)
        self.eps_decay = torch.tensor(eps_decay).to(device)
        self.tau = torch.tensor(tau).to(device)
        self.batch_size = batch_size
        self.replay = ReplayBuffer(storage=ListStorage(max_size=1024),batch_size=self.batch_size)
        self.q_optim = []
        self.p_optim = []
        self.threshold = threshold
        self.device = device
        self.steps = 0
        self.agents = []
        # try:
        #     self.training_logs = pd.read_csv("./training_record_MADDPG.csv")
        #     self.performance_logs = pd.read_csv("./performance_record_MADDPG.csv")
        # except Exception as e:
        #     self.training_logs = pd.DataFrame()
        #     self.performance_logs = pd.DataFrame()

        for i in range(0,agent_num):
            self.agents.append(Agent(i,action_size,obs_size,agent_num,device))
            self.agents[i].load_checkpoint()



    def add_to_replay(self,obs_old:torch.tensor, action:torch.tensor, reward:torch.tensor,
                      obs_new:torch.tensor):
        """

        @param obs_old:
        @type obs_old:
        @param action:
        @type action:
        @param reward:
        @type reward:
        @param obs_new:
        @type obs_new:
        @return:
        @rtype:
        """
        reward = convert_dict_to_tensors(reward).to("cpu")
        obs_old = convert_dict_to_tensors(obs_old).to("cpu")
        obs_new = convert_dict_to_tensors(obs_new).to("cpu")
        act = []
        for a in action:
            act.append(a.detach().cpu())

        if reward.size()[0]==0:
            return
        self.replay.add(TensorDict({"obs":obs_old, "act":act, "rew":reward, "nobs":obs_new}))


    def get_action(self,obs):
        """

        @param obs:
        @type obs:
        @return:
        @rtype:
        """
        obs = convert_dict_to_tensors(obs).to(self.device)
        agent_actions = []

        for ind,a in enumerate(self.agents):
            agent_actions.append(a.get_next_action(obs[ind]).to("cpu"))

        return agent_actions


    def update(self):
        """

        @return:
        @rtype:
        """
        if len(self.replay) < self.threshold:
            return None

        if not len(self.replay) % self.threshold == 0:
            return None

        # print("Updating")
        # t1 =time.time()

        num_agents = len(self.agents)
        samples = self.replay.sample()

        rew_batch = [
            torch.stack([torch.tensor([s["rew"][i]]) for s in samples]).to(self.device)
            for i in range(num_agents)
        ]
        obs_batch = [
            torch.stack([s["obs"][i] for s in samples]).to(self.device)
            for i in range(num_agents)
        ]
        nobs_batch = [
            torch.stack([s["nobs"][i] for s in samples]).to(self.device)
            for i in range(num_agents)
        ]
        act_batch = [
            torch.stack([s["act"][i] for s in samples]).to(self.device)
            for i in range(num_agents)
        ]


        nact_batch = [
            self.agents[i].get_action_target(nobs_batch[i])
            for i in range(num_agents)
        ]

        gobs_batch = torch.cat(obs_batch, dim=1)
        gact_batch = torch.cat(act_batch, dim=1)
        gnobs_batch = torch.cat(nobs_batch, dim=1)
        gnact_batch = torch.cat(nact_batch, dim=1)

        q_target_input = torch.cat([gnobs_batch,gnact_batch], dim=1)
        q_input = torch.cat([gobs_batch, gact_batch],dim=1)
        mean_q_loss = 0
        for i, agent in enumerate(self.agents):

            y_target =  rew_batch[i] + self.gamma * agent.q_function_target(q_target_input)
            # policy_input = torch.cat([obs_batch[i], act_batch[i]], dim=1)  rew_batch[i] +
            q_pred = agent.get_reward(q_input)

            loss_q = torch.nn.MSELoss()(q_pred, y_target)
            mean_q_loss+=loss_q
            agent.q_grad.zero_grad()
            loss_q.backward(retain_graph=True)
            agent.q_grad.step()

            cur_policy = act_batch[i]
            pol_weight = torch.nn.functional.gumbel_softmax(cur_policy, hard=True)

            cur_pol = (cur_policy * pol_weight).sum(dim=1)
            log_pol = torch.log(cur_pol).reshape((self.batch_size,1))

            q_value = agent.get_reward(q_input)
            # TODO element wise multiply
            exp_ret = - (log_pol *q_value).mean()

            agent.policy_grad.zero_grad()
            exp_ret.backward(retain_graph=True)
            agent.policy_grad.step()

            # for j,s in enumerate(samples):
            #     sample = s.to(self.device)
            #     for i,agent in enumerate(self.agents):
            #         rew[i].append(sample["rew"][i])
            #         obs[i].append(sample["obs"][i])
            #         nobs[i].append(sample["nobs"][i])
            #         act_curr[i].append(sample["act"][i])
            #         nact[i].append(agent.get_action_target(nobs[i][-1]))
            #         gnobs[j].extend(sample["nobs"][i])
            #         gnact[j].extend(nact[i][-1])
            #         gact_curr[j].extend(act_curr[i][-1])
            #         gobs[j].extend(obs[i][-1])
            #         gact[j].extend(sample["act"][i])


        # samples = self.replay.sample()
        # gnobs = [[] for _ in samples]
        # gnact = [[] for _ in samples]
        #
        # gact_curr = [[] for _ in samples]
        # act_curr = [[] for _ in self.agents]
        # gobs = [[] for _ in samples]
        # gact = [[] for _ in samples]
        # rew = [[] for _ in self.agents]
        # nact = [[] for _ in self.agents]
        # nobs = [[] for _ in self.agents]
        # obs = [[] for _ in self.agents]
        #
        # for j,s in enumerate(samples):
        #     sample = s.to(self.device)
        #     for i,agent in enumerate(self.agents):
        #         rew[i].append(sample["rew"][i])
        #         obs[i].append(sample["obs"][i])
        #         nobs[i].append(sample["nobs"][i])
        #         act_curr[i].append(sample["act"][i])
        #         nact[i].append(agent.get_action_target(nobs[i][-1]))
        #         gnobs[j].extend(sample["nobs"][i])
        #         gnact[j].extend(nact[i][-1])
        #         gact_curr[j].extend(act_curr[i][-1])
        #         gobs[j].extend(obs[i][-1])
        #         gact[j].extend(sample["act"][i])
        #
        # print("time taken to process data",time.time() -t1)
        # t1 = time.time()
        # for i,agent in enumerate(self.agents):
        #     loss_q = torch.nn.MSELoss()
        #     q = []
        #     y = []
        #     y = rew[i] + self.gamma*agent.q_function_target( torch.cat((sample["nobs"],gnact), dim=1))
        #     for j in range(self.batch_size):
        #         qt_inp = torch.tensor(gnobs[i] + gnact[i]).to(self.device)
        #         q_inp = torch.tensor(gobs[i] + gact[i]).to(self.device)
        #         temp = torch.add(rew[i][j], self.gamma*agent.q_function_target(qt_inp))
        #         y.append(temp)
        #         temp = agent.get_reward(q_inp)
        #         q.append(temp)
        #
        #     agent.q_grad.zero_grad()
        #
        #     q= torch.cat(q).to(self.device)
        #     y= torch.cat(y).to(self.device)
        #     loss = loss_q(q,y)
        #     loss.backward()
        #     agent.q_grad.step()
        #
        #     # if i == 0:
        #     #     for name,param in agent.q_function.model.named_parameters():
        #     #         print(name,param.grad)
        #     #     # input()
        #     #     # print(agent.q_function.model.state_dict())
        #     #     # input()
        #
        #     exp_ret = torch.tensor(0,dtype=torch.float32)
        #     for j in range(self.batch_size):
        #         q_inp = torch.tensor(gobs[j]+gact_curr[j]).to(self.device)
        #         cur_pol = torch.tensor(act_curr[i][j]).to(self.device)
        #         cur_pol_weight = torch.nn.functional.gumbel_softmax(cur_pol,hard=True)
        #         cur_pol  = torch.matmul(cur_pol,cur_pol_weight)
        #         cur_pol = torch.log(cur_pol)
        #         cur_rew = agent.get_reward(q_inp)
        #         exp_ret = exp_ret - cur_pol*cur_rew
        #     exp_ret = torch.divide(exp_ret, self.batch_size)
        #     agent.policy_grad.zero_grad()
        #     exp_ret.backward()
        #     agent.policy_grad.step()
        #     print("Time take for rest calculations and grad descent",time.time()-t1)

            # if i == 0:
            # for name,param in agent.policy.model.named_parameters():
            #     print(name,param.grad)
            #     input()
            #     print(agent.policy.model.state_dict())
            #     input()
            # input()
            for target_param, param in zip(agent.policy_target.model.parameters(), agent.policy.model.parameters()):
                target_param.data.copy_(target_param.data * (1.0 - self.tau) + param.data * self.tau)

            for target_param, param in zip(agent.q_function_target.model.parameters(), agent.q_function.model.parameters()):
                target_param.data.copy_(target_param.data * (1.0 - self.tau) + param.data * self.tau)

            agent.save_checkpoint(None,None)
            # self.training_logs = pd.concat((self.training_logs, pd.DataFrame({"step":self.steps,"agent_num":i,"mean_q_loss":loss_q.item(),"mean_exp_return":exp_ret.item()})),ignore_index=True)
        return mean_q_loss


    # def save_results(self):
    #     self.training_logs.to_csv("./training_record_MADDPG.csv")
    #     self.performance_logs.to_csv("./performance_record_MADDPG.csv")