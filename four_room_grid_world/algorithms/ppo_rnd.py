# Copyright (c) 2018-2022, NVIDIA Corporation
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

# docs and experiment results can be found at https://docs.cleanrl.dev/rl-algorithms/ppo/#ppo_continuous_action_isaacgympy
import argparse
import os
import random
import time
from distutils.util import strtobool

import gymnasium as gym
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from matplotlib import pyplot as plt
from torch.distributions import Categorical
from collections import deque
import torch.nn.functional as F
from copy import deepcopy

from torch.utils.tensorboard import SummaryWriter

from four_room_grid_world.env_gymnasium.StateVisitCountWrapper import StateVisitCountWrapper
from four_room_grid_world.util.plot_util import plot_heatmap, get_trajectories, plot_trajectories, create_plot_env, \
    calculate_states_entropy, is_last_step_in_last_epoch, visit_count_dict_to_list

import four_room_grid_world.env_gymnasium.registration  # Do not remove this import

ENV_SIZE = 50

# Adopted from Isaac ppo_rnd

def parse_args():
    # fmt: off
    parser = argparse.ArgumentParser()
    parser.add_argument("--exp-name", type=str, default=os.path.basename(__file__).rstrip(".py"),
                        help="the name of this experiment")
    parser.add_argument("--seed", type=int, default=0,
                        help="seed of the experiment")
    parser.add_argument("--torch-deterministic", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
                        help="if toggled, `torch.backends.cudnn.deterministic=False`")
    parser.add_argument("--cuda", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
                        help="if toggled, cuda will be enabled by default")
    parser.add_argument("--track", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
                        help="if toggled, this experiment will be tracked with Weights and Biases")
    parser.add_argument("--wandb-project-name", type=str, default="RLE",
                        help="the wandb's project name")
    parser.add_argument("--wandb-entity", type=str, default=None,
                        help="the entity (team) of wandb's project")
    parser.add_argument("--gpu-id", type=int, default=0,
                        help="ID of GPU to use")

    # Algorithm specific arguments
    parser.add_argument("--env-id", type=str, default="advtop/FourRoomGridWorld-v0",
                        help="the id of the environment")
    parser.add_argument("--total-timesteps", type=int, default=2_500_000,
                        help="total timesteps of the experiments")
    parser.add_argument("--learning-rate", type=float, default=0.001,
                        help="the learning rate of the optimizer")
    parser.add_argument("--num-envs", type=int, default=32,
                        help="the number of parallel game environments")
    parser.add_argument("--num-steps", type=int, default=128,
                        help="the number of steps to run in each environment per policy rollout")
    parser.add_argument("--anneal-lr", type=lambda x: bool(strtobool(x)), default=False, nargs="?", const=True,
                        help="Toggle learning rate annealing for policy and value networks")
    parser.add_argument("--gamma", type=float, default=0.99,
                        help="the discount factor gamma")
    parser.add_argument("--gae-lambda", type=float, default=0.95,
                        help="the lambda for the general advantage estimation")
    parser.add_argument("--num-minibatches", type=int, default=4,
                        help="the number of mini-batches")
    parser.add_argument("--update-epochs", type=int, default=4,
                        help="the K epochs to update the policy")
    parser.add_argument("--norm-adv", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
                        help="Toggles advantages normalization")
    parser.add_argument("--clip-coef", type=float, default=0.2,
                        help="the surrogate clipping coefficient")
    parser.add_argument("--clip-vloss", type=lambda x: bool(strtobool(x)), default=True, nargs="?", const=True,
                        help="Toggles whether or not to use a clipped loss for the value function, as per the paper.")
    parser.add_argument("--ent-coef", type=float, default=0.01,
                        help="coefficient of the entropy")
    parser.add_argument("--vf-coef", type=float, default=0.5,
                        help="coefficient of the value function")
    parser.add_argument("--max-grad-norm", type=float, default=0.5,
                        help="the maximum norm for the gradient clipping")
    parser.add_argument("--target-kl", type=float, default=None,
                        help="the target KL divergence threshold")

    parser.add_argument("--reward-scaler", type=float, default=1,
                        help="the scale factor applied to the reward during training")
    parser.add_argument("--record-video-step-frequency", type=int, default=1464,
                        help="the frequency at which to record the videos")
    parser.add_argument("--reward-free", type=str, default="True",
                        help="whether to use the version of the four room environment that does not have any rewards")
    parser.add_argument("--tags", nargs="*", type=str, default=["PPO_RND"],
                        help="a list of tags for wanddb")
    parser.add_argument("--max-episode-steps", type=int, default=1_000,
                        help="maximum number of steps per episode")

    # RND arguments
    parser.add_argument("--update-proportion", type=float, default=0.75,
                        help="proportion of exp used for predictor update")
    parser.add_argument("--int-coef", type=float, default=1.0,
                        help="coefficient of extrinsic reward")
    parser.add_argument("--ext-coef", type=float, default=1.0,
                        help="coefficient of intrinsic reward")
    parser.add_argument("--int-gamma", type=float, default=0.99,
                        help="Intrinsic reward discount rate")
    parser.add_argument("--num-iterations-obs-norm-init", type=int, default=50,
                        help="number of iterations to initialize the observations normalization parameters")

    args = parser.parse_args()
    args.batch_size = int(args.num_envs * args.num_steps)
    args.minibatch_size = int(args.batch_size // args.num_minibatches)
    # fmt: on
    return args


# RunningMeanStd code (from OpenAI baselines) - using torch tensor instead of numpy
class RunningMeanStd:
    """Tracks the mean, variance and count of values."""

    # https://en.wikipedia.org/wiki/Algorithms_for_calculating_variance#Parallel_algorithm
    def __init__(self, epsilon=1e-4, shape=(), device="cpu"):
        """Tracks the mean, variance and count of values."""
        self.mean = torch.zeros(shape, dtype=torch.float64).to(device)
        self.var = torch.ones(shape, dtype=torch.float64).to(device)
        self.count = epsilon

    def update(self, x):
        """Updates the mean, var and count from a batch of samples."""
        batch_mean = torch.mean(x, dim=0)
        batch_var = torch.var(x, dim=0)
        batch_count = x.shape[0]
        self.update_from_moments(batch_mean, batch_var, batch_count)

    def update_from_moments(self, batch_mean, batch_var, batch_count):
        """Updates from batch mean, variance and count moments."""
        self.mean, self.var, self.count = update_mean_var_count_from_moments(
            self.mean, self.var, self.count, batch_mean, batch_var, batch_count
        )


def update_mean_var_count_from_moments(
        mean, var, count, batch_mean, batch_var, batch_count
):
    """Updates the mean, var and count using the previous mean, var, count and batch values."""
    delta = batch_mean - mean
    tot_count = count + batch_count

    new_mean = mean + delta * batch_count / tot_count
    m_a = var * count
    m_b = batch_var * batch_count
    M2 = m_a + m_b + torch.square(delta) * count * batch_count / tot_count
    new_var = M2 / tot_count
    new_count = tot_count

    return new_mean, new_var, new_count


def layer_init(layer, std=np.sqrt(2), bias_const=0.0):
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)
    return layer


class Agent(nn.Module):
    def __init__(self, envs):
        super().__init__()
        self.critic_base = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), 64)),
            nn.Tanh(),
            layer_init(nn.Linear(64, 64)),
            nn.Tanh(),
        )
        self.actor = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), 64)),
            nn.Tanh(),
            layer_init(nn.Linear(64, 64)),
            nn.Tanh(),
            layer_init(nn.Linear(64, envs.single_action_space.n), std=0.01),
        )
        self.critic_ext = layer_init(nn.Linear(64, 1),
                                     std=1.0)
        self.critic_int = layer_init(nn.Linear(64, 1),
                                     std=1.0)

    def get_value(self, x):
        hidden = self.critic_base(x)
        return self.critic_ext(hidden), self.critic_int(hidden)

    def get_action_and_value(self, x, action=None):
        logits = self.actor(x)
        probs = Categorical(logits=logits)
        if action is None:
            action = probs.sample()

        hidden = self.critic_base(x)

        return action, probs.log_prob(action), probs.entropy(), self.critic_ext(hidden), self.critic_int(
            hidden)


class RNDModel(nn.Module):
    def __init__(self, obs_shape, output_size):
        super().__init__()

        self.obs_shape = obs_shape
        self.output_size = output_size
        self.width = 256  # Originally 64
        self.target_width = 64

        # Prediction network
        self.predictor = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), self.width)),
            nn.ReLU(),
            layer_init(nn.Linear(self.width, self.width)),
            nn.ReLU(),
            layer_init(nn.Linear(self.width, self.output_size)),
            nn.ReLU(),
            layer_init(nn.Linear(self.output_size, self.output_size)),
            nn.ReLU(),
            layer_init(nn.Linear(self.output_size, self.output_size), std=0.01),
        )

        # Target network
        self.target = nn.Sequential(
            layer_init(nn.Linear(np.array(envs.single_observation_space.shape).prod(), self.target_width)),
            nn.ReLU(),
            layer_init(nn.Linear(self.target_width, self.output_size)),
        )

        # target network is not trainable
        for param in self.target.parameters():
            param.requires_grad = False

    def forward(self, next_obs):
        target_feature = self.target(next_obs)
        predict_feature = self.predictor(next_obs)

        return predict_feature, target_feature


class RewardForwardFilter:
    def __init__(self, gamma):
        self.rewems = None
        self.gamma = gamma

    def update(self, rews, not_done=None):
        if not_done is None:
            if self.rewems is None:
                self.rewems = rews
            else:
                self.rewems = self.rewems * self.gamma + rews
            return self.rewems
        else:
            if self.rewems is None:
                self.rewems = rews
            else:
                mask = torch.where(not_done == 1.0)
                self.rewems[mask] = self.rewems[mask] * self.gamma + rews[mask]
            return deepcopy(self.rewems)


def make_env(env_id, idx, run_name):
    def thunk():
        env = gym.make(env_id, max_episode_steps=args.max_episode_steps, size=ENV_SIZE, is_reward_free=args.reward_free)
        env = gym.wrappers.RecordEpisodeStatistics(env)
        return env

    return thunk


if __name__ == "__main__":
    args = parse_args()

    if args.reward_free == "True":
        args.reward_free = True
    elif args.reward_free == "False":
        args.reward_free = False
    else:
        raise RuntimeError("Invalid reward-free parameter")

    if args.reward_free:
        args.tags.append("REWARD_FREE")
    else:
        args.tags.append("NOT_REWARD_FREE")

    run_name = f"{args.env_id}__{args.exp_name}__{args.seed}__{int(time.time())}"
    if args.track:
        import wandb

        wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            # sync_tensorboard=True,
            config=vars(args),
            name=run_name,
            # monitor_gym=True,
            save_code=True,
            tags=args.tags,
        )

    writer = SummaryWriter(f"runs/{run_name}")
    writer.add_text(
        "hyperparameters",
        "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()])),
    )

    # TRY NOT TO MODIFY: seeding
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic

    device = torch.device("cuda" if torch.cuda.is_available() and args.cuda else "cpu")

    # env setup
    envs = gym.vector.SyncVectorEnv(
        [make_env(args.env_id, i, run_name) for i in range(args.num_envs)],
    )
    assert isinstance(envs.single_action_space, gym.spaces.Discrete), "only discrete action space is supported"

    envs = StateVisitCountWrapper(envs)

    plot_env = create_plot_env(args.env_id, ENV_SIZE, args.reward_free)

    agent = Agent(envs).to(device)

    rnd_output_size = 256
    rnd_model = RNDModel(envs.single_observation_space.shape, rnd_output_size).to(device)
    combined_parameters = list(agent.parameters()) + list(rnd_model.predictor.parameters())
    optimizer = optim.Adam(
        combined_parameters,
        lr=args.learning_rate,
        eps=1e-5
    )

    obs_rms = RunningMeanStd(shape=envs.single_observation_space.shape, device=device)

    ext_reward_rms = RunningMeanStd(device=device)
    ext_discounted_reward = RewardForwardFilter(args.gamma)
    int_reward_rms = RunningMeanStd(device=device)
    int_discounted_reward = RewardForwardFilter(args.int_gamma)

    # ALGO Logic: Storage setup
    obs = torch.zeros((args.num_steps, args.num_envs) + envs.single_observation_space.shape, dtype=torch.float).to(
        device)
    actions = torch.zeros((args.num_steps, args.num_envs) + envs.single_action_space.shape, dtype=torch.float).to(
        device)
    logprobs = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    rewards = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    curiosity_rewards = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    dones = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)

    ext_values = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    int_values = torch.zeros((args.num_steps, args.num_envs), dtype=torch.float).to(device)
    true_rewards = torch.zeros_like(rewards, dtype=torch.float).to(device)

    # Logging setup
    num_done_envs = 512
    avg_returns = deque(maxlen=num_done_envs)
    avg_ep_lens = deque(maxlen=num_done_envs)
    avg_true_returns = deque(maxlen=num_done_envs)  # returns from without reward shaping

    # TRY NOT TO MODIFY: start the game
    global_step = 0
    start_time = time.time()
    next_obs, _ = envs.reset()
    next_done = torch.zeros(args.num_envs, dtype=torch.float).to(device)
    num_updates = args.total_timesteps // args.batch_size

    print("Start to initialize observation normalization parameter....")
    next_ob = []
    for step in range(args.num_steps * args.num_iterations_obs_norm_init):
        # Sample a random action for all parallel environments with shape (num_envs, action_shape)
        action = torch.tensor(
            np.array([envs.action_space.sample() for _ in range(args.num_envs)]), dtype=torch.float).to(device)

        next_obs, _, next_done, truncations, _ = envs.step(
            action.cpu().numpy()[0])  # [0] to remove unnecessary dimension
        next_obs = torch.Tensor(next_obs).to(device)
        next_done = torch.Tensor(next_done).to(device)

        next_ob.append(next_obs)
        if step % args.num_steps == 0:
            next_ob = torch.stack(next_ob)  # (num_steps, num_envs, obs_shape)
            next_ob = next_ob.view(-1, *envs.single_observation_space.shape)
            obs_rms.update(next_ob)
            next_ob = []

    print("End to initialize observation normalization parameter....")

    for update in range(1, num_updates + 1):
        it_start_time = time.time()
        # Annealing the rate if instructed to do so.
        if args.anneal_lr:
            frac = 1.0 - (update - 1.0) / num_updates
            lrnow = frac * args.learning_rate
            optimizer.param_groups[0]["lr"] = lrnow

        for step in range(0, args.num_steps):
            global_step += 1 * args.num_envs
            obs[step] = next_obs
            dones[step] = next_done

            # ALGO LOGIC: action logic
            with torch.no_grad():
                action, logprob, _, value_ext, value_int = agent.get_action_and_value(next_obs)
                ext_values[step], int_values[step] = (
                    value_ext.flatten(),
                    value_int.flatten(),
                )

            actions[step] = action
            logprobs[step] = logprob

            # TRY NOT TO MODIFY: execute the game and log data.
            next_obs, next_rewards, next_done, truncations, infos = envs.step(action.cpu().numpy())
            next_obs = torch.Tensor(next_obs).to(device)
            next_done = np.logical_or(next_done, truncations)
            next_done = torch.Tensor(next_done).to(device)
            next_rewards = torch.Tensor(next_rewards).to(device)

            rewards[step] = next_rewards

            if global_step == 500_000 or is_last_step_in_last_epoch(update, num_updates, step, args.num_steps):
                plot_heatmap(infos, global_step, ENV_SIZE, f"runs/{run_name}")
                if args.track:
                    wandb.log({"State visit heatmap": wandb.Image(plt.gcf())}, global_step)

            if global_step == 500_000 or global_step == 1_500_000 or is_last_step_in_last_epoch(update, num_updates, step, args.num_steps):
                trajectories = get_trajectories(plot_env, agent, device)
                plot_trajectories(global_step, trajectories, ENV_SIZE, plot_env.x_wall_gap_offset, plot_env.y_wall_gap_offset, f"runs/{run_name}")
                if args.track:
                    wandb.log({"trajectories": wandb.Image(plt.gcf())}, global_step)

            if is_last_step_in_last_epoch(update, num_updates, step, args.num_steps):
                if args.track:
                    wandb.log({"visit_counts": visit_count_dict_to_list(infos["visit_counts"], ENV_SIZE)})

            for idx, d in enumerate(next_done):
                if d:
                    episodic_return = infos["final_info"][idx]["episode"]["r"].item()
                    episode_length = infos["final_info"][idx]["episode"]["l"].item()
                    avg_returns.append(infos["final_info"][idx]["episode"]["r"].item())
                    avg_ep_lens.append(episode_length)

                    print(f"global_step={global_step}, episodic_return={episodic_return}")
                    writer.add_scalar("charts/episodic_return", episodic_return, global_step)
                    writer.add_scalar("charts/episodic_length", episode_length, global_step)
                    if args.track:
                        wandb.log({"charts/episodic_return": episodic_return,
                                   "charts/episodic_length": episode_length},
                                  step=global_step)

            rnd_next_obs = (
                (
                        (next_obs.reshape(args.num_envs, *envs.single_observation_space.shape) - obs_rms.mean.to(
                            device))
                        / torch.sqrt(obs_rms.var.to(device))
                ).clip(-5, 5)
            ).float()
            target_next_feature = rnd_model.target(rnd_next_obs)
            predict_next_feature = rnd_model.predictor(rnd_next_obs)
            curiosity_rewards[step] = ((target_next_feature - predict_next_feature).pow(2).sum(1) / 2).data

        state_visit_entropy = calculate_states_entropy(infos, global_step, ENV_SIZE)
        if args.track:
            wandb.log({"charts/state_visit_entropy": state_visit_entropy}, step=global_step)

        not_dones = 1.0 - dones
        ext_reward_per_env = torch.stack(
            [ext_discounted_reward.update(rewards[i], not_dones[i]) for i in range(args.num_steps)]
        ).to(device)
        ext_reward_rms.update(ext_reward_per_env.flatten())
        rewards /= torch.sqrt(ext_reward_rms.var)

        int_reward_per_env = torch.stack(
            [int_discounted_reward.update(curiosity_rewards[i], not_dones[i]) for i in range(args.num_steps)]
        ).to(device)
        int_reward_rms.update(int_reward_per_env.flatten())
        curiosity_rewards /= torch.sqrt(int_reward_rms.var)

        # bootstrap value if not done
        with torch.no_grad():
            next_value_ext, next_value_int = agent.get_value(next_obs)
            next_value_ext, next_value_int = next_value_ext.reshape(1, -1), next_value_int.reshape(1, -1)
            ext_advantages = torch.zeros_like(rewards, device=device)
            int_advantages = torch.zeros_like(curiosity_rewards, device=device)
            ext_lastgaelam = 0
            int_lastgaelam = 0
            for t in reversed(range(args.num_steps)):
                if t == args.num_steps - 1:
                    ext_nextnonterminal = 1.0 - next_done.float()
                    int_nextnonterminal = 1.0
                    ext_nextvalues = next_value_ext
                    int_nextvalues = next_value_int
                else:
                    ext_nextnonterminal = 1.0 - dones[t + 1]
                    int_nextnonterminal = 1.0
                    ext_nextvalues = ext_values[t + 1]
                    int_nextvalues = int_values[t + 1]

                ext_delta = rewards[t] + args.gamma * ext_nextvalues * ext_nextnonterminal - ext_values[t]
                int_delta = curiosity_rewards[t] + args.int_gamma * int_nextvalues * int_nextnonterminal - int_values[t]
                ext_advantages[t] = ext_lastgaelam = (
                        ext_delta + args.gamma * args.gae_lambda * ext_nextnonterminal * ext_lastgaelam
                )
                int_advantages[t] = int_lastgaelam = (
                        int_delta + args.int_gamma * args.gae_lambda * int_nextnonterminal * int_lastgaelam
                )
            ext_returns = ext_advantages + ext_values
            int_returns = int_advantages + int_values

        # flatten the batch
        b_obs = obs.reshape((-1,) + envs.single_observation_space.shape)
        b_logprobs = logprobs.reshape(-1)
        b_actions = actions.reshape((-1,) + envs.single_action_space.shape)
        b_ext_advantages = ext_advantages.reshape(-1)
        b_int_advantages = int_advantages.reshape(-1)
        b_ext_returns = ext_returns.reshape(-1)
        b_int_returns = int_returns.reshape(-1)
        b_ext_values = ext_values.reshape(-1)
        b_int_values = int_values.reshape(-1)

        b_advantages = b_int_advantages * args.int_coef + b_ext_advantages * args.ext_coef

        obs_rms.update(b_obs.view(-1, *envs.single_observation_space.shape))

        rnd_next_obs = (
            (
                    (b_obs.reshape(-1, *envs.single_observation_space.shape) - obs_rms.mean.to(device))
                    / torch.sqrt((obs_rms.var))
            ).clip(-5, 5)
        ).float()

        # Optimizing the policy and value network
        clipfracs = []
        for epoch in range(args.update_epochs):
            b_inds = torch.randperm(args.batch_size, device=device)
            for start in range(0, args.batch_size, args.minibatch_size):
                end = start + args.minibatch_size
                mb_inds = b_inds[start:end]

                predict_next_state_feature, target_next_state_feature = rnd_model(rnd_next_obs[mb_inds])
                forward_loss = F.mse_loss(
                    predict_next_state_feature, target_next_state_feature.detach(), reduction="none"
                ).mean(-1)

                mask = torch.rand(len(forward_loss), device=device)
                mask = (mask < args.update_proportion).type(torch.FloatTensor).to(device)
                forward_loss = (forward_loss * mask).sum() / torch.max(
                    mask.sum(), torch.tensor([1], device=device, dtype=torch.float32)
                )
                _, newlogprob, entropy, new_ext_values, new_int_values = agent.get_action_and_value(
                    b_obs[mb_inds], b_actions[mb_inds]
                )

                logratio = newlogprob - b_logprobs[mb_inds]
                ratio = logratio.exp()

                with torch.no_grad():
                    # calculate approx_kl http://joschu.net/blog/kl-approx.html
                    old_approx_kl = (-logratio).mean()
                    approx_kl = ((ratio - 1) - logratio).mean()
                    clipfracs += [((ratio - 1.0).abs() > args.clip_coef).float().mean().item()]

                mb_advantages = b_advantages[mb_inds]
                if args.norm_adv:
                    mb_advantages = (mb_advantages - mb_advantages.mean()) / (mb_advantages.std() + 1e-8)

                # Policy loss
                pg_loss1 = -mb_advantages * ratio
                pg_loss2 = -mb_advantages * torch.clamp(ratio, 1 - args.clip_coef, 1 + args.clip_coef)
                pg_loss = torch.max(pg_loss1, pg_loss2).mean()

                # Value loss
                # newvalue = newvalue.view(-1)
                new_ext_values, new_int_values = new_ext_values.view(-1), new_int_values.view(-1)
                if args.clip_vloss:
                    ext_v_loss_unclipped = (new_ext_values - b_ext_returns[mb_inds]) ** 2
                    ext_v_clipped = b_ext_values[mb_inds] + torch.clamp(
                        new_ext_values - b_ext_values[mb_inds],
                        -args.clip_coef,
                        args.clip_coef,
                    )
                    ext_v_loss_clipped = (ext_v_clipped - b_ext_returns[mb_inds]) ** 2
                    ext_v_loss_max = torch.max(ext_v_loss_unclipped, ext_v_loss_clipped)
                    ext_v_loss = 0.5 * ext_v_loss_max.mean()
                else:
                    ext_v_loss = 0.5 * ((new_ext_values - b_ext_returns[mb_inds]) ** 2).mean()

                int_v_loss = 0.5 * ((new_int_values - b_int_returns[mb_inds]) ** 2).mean()
                v_loss = ext_v_loss + int_v_loss

                entropy_loss = entropy.mean()
                loss = pg_loss - args.ent_coef * entropy_loss + v_loss * args.vf_coef + forward_loss

                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(combined_parameters, args.max_grad_norm)
                optimizer.step()

            if args.target_kl is not None:
                if approx_kl > args.target_kl:
                    break

        it_end_time = time.time()

        # TRY NOT TO MODIFY: record rewards for plotting purposes
        data = {}
        print("SPS:", int(global_step / (time.time() - start_time)))

        data["charts/iterations"] = update
        data["charts/learning_rate"] = optimizer.param_groups[0]["lr"]
        data["losses/ext_value_loss"] = ext_v_loss.item()
        data["losses/int_value_loss"] = int_v_loss.item()
        data["losses/policy_loss"] = pg_loss.item()
        data["losses/entropy"] = entropy_loss.item()
        data["losses/old_approx_kl"] = old_approx_kl.item()
        data["losses/clipfrac"] = np.mean(clipfracs)
        data["losses/fwd_loss"] = forward_loss.item()
        data["losses/approx_kl"] = approx_kl.item()
        data["losses/all_loss"] = loss.item()
        data["charts/SPS"] = int(global_step / (time.time() - start_time))

        data["rewards/rewards_mean"] = rewards.mean().item()
        data["rewards/rewards_max"] = rewards.max().item()
        data["rewards/rewards_min"] = rewards.min().item()
        data["rewards/true_rewards_mean"] = true_rewards.mean().item()
        data["rewards/true_rewards_max"] = true_rewards.max().item()
        data["rewards/true_rewards_min"] = true_rewards.min().item()
        data["rewards/intrinsic_rewards_mean"] = curiosity_rewards.mean().item()
        data["rewards/intrinsic_rewards_max"] = curiosity_rewards.max().item()
        data["rewards/intrinsic_rewards_min"] = curiosity_rewards.min().item()

        data["returns/advantages"] = b_advantages.mean().item()
        data["returns/ext_advantages"] = b_ext_advantages.mean().item()
        data["returns/int_advantages"] = b_int_advantages.mean().item()
        data["returns/ret_ext"] = b_ext_returns.mean().item()
        data["returns/ret_int"] = b_int_returns.mean().item()
        data["returns/values_ext"] = b_ext_values.mean().item()
        data["returns/values_int"] = b_int_values.mean().item()

        data["charts/traj_len"] = np.mean(avg_ep_lens)
        data["charts/max_traj_len"] = np.max(avg_ep_lens, initial=0)
        data["charts/min_traj_len"] = np.min(avg_ep_lens, initial=0)
        data["charts/time_per_it"] = it_end_time - it_start_time

        data["charts/game_score"] = np.mean(avg_returns)
        data["charts/max_game_score"] = np.max(avg_returns, initial=0)
        data["charts/min_game_score"] = np.min(avg_returns, initial=0)

        data["charts/true_episode_return"] = np.mean(avg_true_returns)
        data["charts/max_true_episode_return"] = np.max(avg_true_returns, initial=0)
        data["charts/min_true_episode_return"] = np.min(avg_true_returns, initial=0)

        writer.add_scalar("charts/learning_rate", optimizer.param_groups[0]["lr"], global_step)
        writer.add_scalar("losses/value_loss", v_loss.item(), global_step)
        writer.add_scalar("losses/policy_loss", pg_loss.item(), global_step)
        writer.add_scalar("losses/entropy", entropy_loss.item(), global_step)
        writer.add_scalar("losses/old_approx_kl", old_approx_kl.item(), global_step)
        writer.add_scalar("losses/approx_kl", approx_kl.item(), global_step)
        writer.add_scalar("losses/clipfrac", np.mean(clipfracs), global_step)
        writer.add_scalar("charts/SPS", int(global_step / (time.time() - start_time)), global_step)

        if args.track:
            wandb.log(data, step=global_step)

    envs.close()
    writer.close()
