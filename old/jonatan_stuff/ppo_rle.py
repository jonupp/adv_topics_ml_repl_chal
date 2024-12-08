# RUN WITH THESE ARGUMENTS: --env_id advtop/FourRoomGridWorld-v0 --total_timesteps 2500000 --learning_rate 0.001 --num_envs 32

#(base) jonatan@Jonatans-MacBook-Pro adv_topics_ml_repl_chal % python four_room_grid_world/algorithms/ppo_rle.py --env_id advtop/FourRoomGridWorld-v0 --total_timesteps 10000000 --learning_rate 0.001 --num_envs 32
#zsh
# docs and experiment results can be found at https://docs.cleanrl.dev/rl-algorithms/ppo/#ppopy
import os
import random
import time
from dataclasses import dataclass

import gymnasium as gym
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import tyro
from gym.wrappers.normalize import RunningMeanStd
from torch.distributions.categorical import Categorical
from torch.utils.tensorboard import SummaryWriter

import four_room_grid_world.env_gymnasium.registration  # Do not remove this import
from four_room_grid_world.env_gymnasium.FourRoomGridWorld import FourRoomGridWorld

# Add these imports at the top
import imageio
from PIL import Image
import matplotlib.pyplot as plt

def record_episode(env, agent, device, max_steps=200, filename="episode.gif"):
    frames = []
    obs, _ = env.reset()
    obs = torch.Tensor(obs).to(device)

    for step in range(max_steps):
        frames.append(env.render())

        with torch.no_grad():
            action, _, _, _ = agent.get_action_and_value(obs)
            action = action.cpu().item()

        obs, _, terminated, truncated, _ = env.step(action)
        obs = torch.Tensor(obs).to(device)

        if terminated or truncated:
            break

    imageio.mimsave(filename, frames, duration=1000/30)

def record_episode_with_probs(env, agent, device, max_steps=200, filename="episode.gif", iteration=0, feature_network=None):
    frames = []
    obs, _ = env.reset()
    obs = torch.Tensor(obs).to(device).unsqueeze(0)

    # Sample a latent vector for this episode
    z = feature_network.sample_z(batch_size=1) if feature_network else None

    # Store action probabilities for plotting
    all_probs = []
    accumulated_reward = 0
    accumulated_intrinsic_reward = 0

    for step in range(max_steps):
        frames.append(env.render())

        with torch.no_grad():
            # Get action and probabilities
            if feature_network:
                combined_input = torch.cat([obs, z], dim=1)
                logits = agent.actor(combined_input)
            else:
                logits = agent.actor(obs)

            # Calculate probabilities properly
            probs = torch.nn.functional.softmax(logits, dim=1)
            action_dist = torch.distributions.Categorical(logits=logits)
            action = action_dist.sample()

            # Store probabilities (make sure to detach and convert to numpy)
            all_probs.append(probs.detach().cpu().numpy()[0])  # [0] to get the first (and only) batch item

            action = action.cpu().item()

        next_obs, reward, terminated, truncated, _ = env.step(action)
        accumulated_reward += reward

        # Compute intrinsic reward if feature network is provided
        if feature_network:
            next_obs_tensor = torch.Tensor(next_obs).unsqueeze(0).to(device)
            intrinsic_reward, _ = feature_network.compute_reward(next_obs_tensor, z)
            accumulated_intrinsic_reward += intrinsic_reward.item()

        obs = torch.Tensor(next_obs).to(device).unsqueeze(0)

        if terminated or truncated:
            break

    # Save the GIF
    imageio.mimsave(filename, frames, duration=1000/30)

    # Plot probability histogram
    if len(all_probs) > 0:
        all_probs = np.array(all_probs)  # Shape should be (num_steps, num_actions)
        action_counts = all_probs.mean(axis=0)  # Average probability for each action

        plt.figure(figsize=(10, 5))
        num_actions = len(action_counts)
        plt.bar(range(num_actions), action_counts)
        title = f'Action Distribution - Iteration {iteration}\n'
        title += f'Extrinsic Reward: {accumulated_reward:.2f}'
        if feature_network:
            title += f'\nIntrinsic Reward: {accumulated_intrinsic_reward:.2f}'
        plt.title(title)
        plt.xlabel('Action')
        plt.ylabel('Average Probability')
        plt.xticks(range(num_actions))
        plt.grid(True)

        # Save the plot
        plot_filename = filename.replace('.gif', '_probs.png')
        plt.savefig(plot_filename)
        plt.close()

        print(f"Episode {iteration} - Extrinsic Reward: {accumulated_reward:.2f}" +
              (f", Intrinsic Reward: {accumulated_intrinsic_reward:.2f}" if feature_network else ""))

@dataclass
class Args:
    """
    Configuration class that defines all hyperparameters and settings:
    - exp_name: Experiment name for logging
    - env_id: The environment to train on
    - total_timesteps: Total number of environment steps to train for
    - learning_rate: Learning rate for the optimizer
    - num_envs: Number of parallel environments
    ... and many other PPO-specific parameters
    """
    exp_name: str = os.path.basename(__file__)[: -len(".py")]
    """the name of this experiment"""
    seed: int = 1
    """seed of the experiment"""
    torch_deterministic: bool = True
    """if toggled, `torch.backends.cudnn.deterministic=False`"""
    cuda: bool = True
    """if toggled, cuda will be enabled by default"""
    track: bool = False
    """if toggled, this experiment will be tracked with Weights and Biases"""
    wandb_project_name: str = "cleanRL"
    """the wandb's project name"""
    wandb_entity: str = None
    """the entity (team) of wandb's project"""
    capture_video: bool = False
    """whether to capture videos of the agent performances (check out `videos` folder)"""

    # Algorithm specific arguments
    env_id: str = "CartPole-v1"
    """the id of the environment"""
    total_timesteps: int = 500000
    """total timesteps of the experiments"""
    learning_rate: float = 0.001 #2.5e-4
    """the learning rate of the optimizer"""
    num_envs: int = 4
    """the number of parallel game environments"""
    num_steps: int = 128
    """the number of steps to run in each environment per policy rollout"""
    anneal_lr: bool = True
    """Toggle learning rate annealing for policy and value networks"""
    gamma: float = 0.99
    """the discount factor gamma"""
    gae_lambda: float = 0.95
    """the lambda for the general advantage estimation"""
    num_minibatches: int = 4
    """the number of mini-batches"""
    update_epochs: int = 4
    """the K epochs to update the policy"""
    norm_adv: bool = True
    """Toggles advantages normalization"""
    clip_coef: float = 0.2
    """the surrogate clipping coefficient"""
    clip_vloss: bool = True
    """Toggles whether or not to use a clipped loss for the value function, as per the paper."""
    ent_coef: float = 0.01
    """coefficient of the entropy"""
    vf_coef: float = 0.5
    """coefficient of the value function"""
    max_grad_norm: float = 0.5
    """the maximum norm for the gradient clipping"""
    target_kl: float = None
    """the target KL divergence threshold"""

    # RLE specific arguments
    int_coef: float = 0.01
    """coefficient for intrinsic rewards from RLE"""
    RLE_FEATURE_SIZE: int = 4 
    """feature size for the RLE"""

    # to be filled in runtime
    batch_size: int = 0
    """the batch size (computed in runtime)"""
    minibatch_size: int = 0
    """the mini-batch size (computed in runtime)"""
    num_iterations: int = 0
    """the number of iterations (computed in runtime)"""


def make_env(env_id, idx, capture_video, run_name):
    """
    Factory function that creates and wraps environments:
    - Sets up video recording if enabled
    - Wraps environment to record episode statistics
    - Configures environment parameters like max steps
    """
    def thunk():
        if capture_video and idx == 0:
            env = gym.make(env_id, render_mode="rgb_array", max_episode_steps=None, size=50)
            env = gym.wrappers.RecordVideo(env, f"videos/{run_name}")
        else:
            env = gym.make(env_id, max_episode_steps=10_000_000, size=50)
        env = gym.wrappers.RecordEpisodeStatistics(env)
        return env

    return thunk


def layer_init(layer, std=np.sqrt(2), bias_const=0.0):
    torch.nn.init.orthogonal_(layer.weight, std)
    torch.nn.init.constant_(layer.bias, bias_const)
    return layer


class FeatureNetwork(nn.Module):
    """
    Feature Network to extract the features from the state
    it learns by linear combination of its params with the policy one
    """
    def __init__(self, envs, feature_size=4, tau=0.005, device='cuda'):
        super().__init__()
        self.device = device
        self.feature_size = feature_size
        self.tau = tau

        # Shared feature extraction layers (same architecture as value network early layers)
        self.shared_layers = nn.Sequential(
            layer_init(nn.Linear(np.prod(envs.single_observation_space.shape), 64)),
            nn.ReLU(),
            layer_init(nn.Linear(64, 64)),
            nn.ReLU(),
        )

        # last layer to produce features
        self.feature_layer = layer_init(nn.Linear(64, feature_size))

        # the mean and std to compute the intrinsic reward
        self.rms = RunningMeanStd(shape=(1, feature_size))
        self.feat_mean = torch.zeros(feature_size).to(device)
        self.feat_std = torch.ones(feature_size).to(device)

    def forward(self, x):
        """I use this to compute the forward step"""
        shared_features = self.shared_layers(x)
        features = self.feature_layer(shared_features)
        return features

    def normalize_features(self, features):
        """normalization as always - added epsilon to avoid division by zero since it fail when action space not that big."""
        return (features - self.feat_mean) / (self.feat_std + 1e-8)

    def compute_reward(self, state, z):
        """
        this will output the intrinsic reward as on paper for a given (state,z)
        """
        with torch.no_grad():
            #we get the unnormalized features and then normalize them
            raw_features = self.forward(state)
            norm_features = self.normalize_features(raw_features)

            # dot product
            reward = (norm_features * z).sum(dim=1)

        return reward, raw_features


    def update_from_policy_net(self, policy_net):
        """
        Here we need to update the feature network as a linear combination of the policy one and itself (as they done on teh paper)
        !!!!!!
        here is something really important, to actually combine them, we can only take the second to last layer of the policy network
        since the first layer of the policy network has also the z vector as input. that is not input in the feature network.
        """
        feature_layers = [layer for layer in self.shared_layers if isinstance(layer, nn.Linear)]
        policy_layers = [layer for layer in policy_net if isinstance(layer, nn.Linear)][:len(feature_layers)]

        for feat_layer, policy_layer in zip(feature_layers, policy_layers):
            for feat_param, policy_param in zip(feat_layer.parameters(), policy_layer.parameters()):
                if feat_param.shape == policy_param.shape:
                    """only lower layers are updated since the upper ones need to control the z latent vector in the policy network"""
                    #print(f"feat_param: {feat_param.shape}, policy_param: {policy_param.shape}")
                    feat_param.data.copy_(
                        self.tau * policy_param.data + (1.0 - self.tau) * feat_param.data
                    )

    def update_stats(self, features):
        """to update the running mean and std as mentioned in the paper - once per episode"""
        self.rms.update(features.detach().cpu().numpy())
        self.feat_mean = torch.from_numpy(self.rms.mean).float().to(self.device)
        self.feat_std = torch.sqrt(torch.from_numpy(self.rms.var)).float().to(self.device)

    def sample_z(self, batch_size=1):
        """unit sphere sampling, they mentioned that does not make much a difference the shape..."""
        z = torch.randn(batch_size, self.feature_size, device=self.device)
        z = z / z.norm(dim=1, keepdim=True)  # Project to unit sphere
        return z



class Agent(nn.Module):
    """
    Actor-Critic network for the PPO algorithm with the latent vector z as input
    """
    def __init__(self, envs):
        super().__init__()

        # (state dims + latent dims)
        state_dims = np.array(envs.single_observation_space.shape).prod()

        # Critic network (state + z -> value)
        self.critic = nn.Sequential(
            layer_init(nn.Linear(state_dims + args.RLE_FEATURE_SIZE, 64)),
            nn.ReLU(),
            layer_init(nn.Linear(64, 64)),
            nn.ReLU(),
            layer_init(nn.Linear(64, 1), std=1.0),
        )

        # Actor network (state + z -> action distribution)
        self.actor = nn.Sequential(
            layer_init(nn.Linear(state_dims + args.RLE_FEATURE_SIZE, 64)),
            nn.ReLU(),
            layer_init(nn.Linear(64, 64)),
            nn.ReLU(),
            layer_init(nn.Linear(64, envs.single_action_space.n), std=0.01),
        )

    def get_value(self, x, z):
        """state + latent vector -> value"""
        combined_input = torch.cat([x, z], dim=1)
        return self.critic(combined_input)

    def get_action_and_value(self, x, z, action=None):
        """state + latent vector -> action and value
        which returns:
            action, logits, entropy, value -> i based on their code to compute the entropy 
        """
        combined_input = torch.cat([x, z], dim=1)
        logits = self.actor(combined_input)
        probs = Categorical(logits=logits)

        if action is None:
            action = probs.sample()

        return action, probs.log_prob(action), probs.entropy(), self.critic(combined_input)


if __name__ == "__main__":
    args = tyro.cli(Args)
    args.batch_size = int(args.num_envs * args.num_steps)
    args.minibatch_size = int(args.batch_size // args.num_minibatches)
    args.num_iterations = args.total_timesteps // args.batch_size
    run_name = f"{args.env_id}__{args.exp_name}__{args.seed}__{int(time.time())}"

    # Set up logging
    if args.track:
        import wandb
        wandb.init(
            project=args.wandb_project_name,
            entity=args.wandb_entity,
            sync_tensorboard=True,
            config=vars(args),
            name=run_name,
            monitor_gym=True,
            save_code=True,
        )
    writer = SummaryWriter(f"runs/{run_name}")
    writer.add_text(
        "hyperparameters",
        "|param|value|\n|-|-|\n%s" % ("\n".join([f"|{key}|{value}|" for key, value in vars(args).items()])),
    )

    # Seeding
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.backends.cudnn.deterministic = args.torch_deterministic

    # Check CUDA availability and set device
    device = torch.device("cuda" if torch.cuda.is_available() and args.cuda else "cpu")
    if args.cuda and not torch.cuda.is_available():
        print("CUDA is not available, using CPU instead")
        args.cuda = False

    # Initialize environment
    envs = gym.vector.SyncVectorEnv(
        [make_env(args.env_id, i, args.capture_video, run_name) for i in range(args.num_envs)],
    )
    assert isinstance(envs.single_action_space, gym.spaces.Discrete), "only discrete action space is supported"

    # NOW RLE:
    #WE INIT THE THREE / TWO NETWORKS:
    feature_network = FeatureNetwork(envs, feature_size=args.RLE_FEATURE_SIZE, device=device).to(device)
    agent = Agent(envs).to(device)
    optimizer = optim.Adam(agent.parameters(), lr=args.learning_rate, eps=1e-5)

    # EPISODE MEMORY
    record_env = gym.make(args.env_id, render_mode="rgb_array", max_episode_steps=None, size=50)

    # Storage setup for experience collection
    obs = torch.zeros((args.num_steps, args.num_envs) + envs.single_observation_space.shape).to(device)
    actions = torch.zeros((args.num_steps, args.num_envs) + envs.single_action_space.shape).to(device)
    logprobs = torch.zeros((args.num_steps, args.num_envs)).to(device)
    rewards = torch.zeros((args.num_steps, args.num_envs)).to(device)
    dones = torch.zeros((args.num_steps, args.num_envs)).to(device)
    values = torch.zeros((args.num_steps, args.num_envs)).to(device)

    # Initialize latent vectors - one per environment
    latent_vectors = feature_network.sample_z(args.num_envs)
    steps_since_z_reset = torch.zeros(args.num_envs, device=device)

    z_reset_frequency = 1280  # reset freq hyperparameter, i added this to control ourselves if we need to explore .

    # Start training
    global_step = 0
    start_time = time.time()
    next_obs, _ = envs.reset(seed=args.seed) #seed the first env
    next_obs = torch.Tensor(next_obs).to(device) #to tensor and to device
    next_done = torch.zeros(args.num_envs).to(device) #zeros for the done vector 
    #remember that the done vector is a boolean vector that indicates if the episode is done for each environment

    for iteration in range(1, args.num_iterations + 1):
        #num_iterations = total_timesteps / num_envs  * num_steps
        #num_iterations: represents the total number of training updates
        #storage of features so we use to update it.
        episode_features = []
        # annealing the learning rate if instructed to do so
        if args.anneal_lr:
            frac = 1.0 - (iteration - 1.0) / args.num_iterations
            lrnow = frac * args.learning_rate
            optimizer.param_groups[0]["lr"] = lrnow

        for step in range(0, args.num_steps):
            #global step is the total number of steps that have been taken in all environments and iterations
            #that means that the total number of episodes total_timesteps / avg_episode_steps.
            global_step += args.num_envs
            obs[step] = next_obs
            dones[step] = next_done

            # ALGO LOGIC: action logic
            with torch.no_grad():
                action, logprob, _, value = agent.get_action_and_value(next_obs, latent_vectors)
                values[step] = value.flatten()
            actions[step] = action
            logprobs[step] = logprob

            # we pass the action to the environment and get the next observation, reward, and other info
            next_obs, reward, terminations, truncations, infos = envs.step(action.cpu().numpy())
            next_done = np.logical_or(terminations, truncations)

            # with the next obs and the latent vector we compute the intrinsic reward
            intrinsic_reward, features = feature_network.compute_reward(
                torch.Tensor(next_obs).to(device),
                latent_vectors
            )
            episode_features.append(features)

            # combine the extrinsic and intrinsic rewards
            combined_reward = torch.tensor(reward).to(device).view(-1) + args.int_coef * intrinsic_reward
            rewards[step] = combined_reward

            # update the feature statistics
            feature_network.update_stats(features)

            # update the latent vectors if needed
            steps_since_z_reset += 1
            reset_z = (steps_since_z_reset >= z_reset_frequency) | torch.tensor(next_done, device=device)
            if reset_z.any():
                # when a new sample z is needed
                new_z = feature_network.sample_z(reset_z.sum())
                latent_vectors[reset_z] = new_z
                steps_since_z_reset[reset_z] = 0

            next_obs, next_done = torch.Tensor(next_obs).to(device), torch.Tensor(next_done).to(device)

            if "final_info" in infos:
                for info in infos["final_info"]:
                    if info and "episode" in info:
                        print(f"global_step={global_step}, episodic_return={info['episode']['r']}")
                        writer.add_scalar("charts/episodic_return", info["episode"]["r"], global_step)
                        writer.add_scalar("charts/episodic_length", info["episode"]["l"], global_step)

        # this is to save the episode recording periodically as a gif
        if iteration % 100 == 0:
            gif_path = f"gifs_rle/episode_{iteration}.gif"
            os.makedirs("gifs_rle", exist_ok=True)
            record_episode_with_probs(
                env=record_env,
                agent=agent,
                device=device,
                filename=gif_path,
                iteration=iteration,
                feature_network=feature_network 
            )
            print(f"Saved episode recording to {gif_path}")

        # returns and the advantages as all policy methods
        with torch.no_grad():
            episode_features = torch.cat(episode_features, dim=0)
            feature_network.update_stats(episode_features)
            feature_network.update_from_policy_net(agent.actor)

            next_value = agent.get_value(next_obs, latent_vectors).reshape(1, -1)
            advantages = torch.zeros_like(rewards).to(device)
            lastgaelam = 0
            for t in reversed(range(args.num_steps)):
                if t == args.num_steps - 1:
                    nextnonterminal = 1.0 - next_done
                    nextvalues = next_value
                else:
                    nextnonterminal = 1.0 - dones[t + 1]
                    nextvalues = values[t + 1]
                delta = rewards[t] + args.gamma * nextvalues * nextnonterminal - values[t]
                advantages[t] = lastgaelam = delta + args.gamma * args.gae_lambda * nextnonterminal * lastgaelam
            returns = advantages + values



        # Flatten the batch
        b_obs = obs.reshape((-1,) + envs.single_observation_space.shape)
        b_logprobs = logprobs.reshape(-1)
        b_actions = actions.reshape((-1,) + envs.single_action_space.shape)
        b_advantages = advantages.reshape(-1)
        b_returns = returns.reshape(-1)
        b_values = values.reshape(-1)
        b_latents = latent_vectors.repeat_interleave(args.num_steps, dim=0)

        # Optimize policy
        clipfracs = []
        for epoch in range(args.update_epochs):
            np.random.shuffle(b_inds := np.arange(args.batch_size))
            for start in range(0, args.batch_size, args.minibatch_size):
                end = start + args.minibatch_size
                mb_inds = b_inds[start:end]

                _, newlogprob, entropy, newvalue = agent.get_action_and_value(
                    b_obs[mb_inds],
                    b_latents[mb_inds],
                    b_actions.long()[mb_inds]
                )

                logratio = newlogprob - b_logprobs[mb_inds]
                ratio = logratio.exp()

                # Policy loss
                mb_advantages = b_advantages[mb_inds]
                if args.norm_adv:
                    mb_advantages = (mb_advantages - mb_advantages.mean()) / (mb_advantages.std() + 1e-8)

                pg_loss1 = -mb_advantages * ratio
                pg_loss2 = -mb_advantages * torch.clamp(ratio, 1 - args.clip_coef, 1 + args.clip_coef)
                pg_loss = torch.max(pg_loss1, pg_loss2).mean()

                # Value loss
                newvalue = newvalue.view(-1)
                if args.clip_vloss:
                    v_loss_unclipped = (newvalue - b_returns[mb_inds]) ** 2
                    v_clipped = b_values[mb_inds] + torch.clamp(
                        newvalue - b_values[mb_inds],
                        -args.clip_coef,
                        args.clip_coef,
                    )
                    v_loss_clipped = (v_clipped - b_returns[mb_inds]) ** 2
                    v_loss_max = torch.max(v_loss_unclipped, v_loss_clipped)
                    v_loss = 0.5 * v_loss_max.mean()
                else:
                    v_loss = 0.5 * ((newvalue - b_returns[mb_inds]) ** 2).mean()

                entropy_loss = entropy.mean()
                loss = pg_loss - args.ent_coef * entropy_loss + v_loss * args.vf_coef

                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(agent.parameters(), args.max_grad_norm)
                optimizer.step()

                with torch.no_grad():
                    old_approx_kl = (-logratio).mean()
                    approx_kl = ((ratio - 1) - logratio).mean()
                    clipfracs += [((ratio - 1.0).abs() > args.clip_coef).float().mean().item()]

            if args.target_kl is not None and approx_kl > args.target_kl:
                break

        # Log metrics
        y_pred, y_true = b_values.cpu().numpy(), b_returns.cpu().numpy()
        var_y = np.var(y_true)
        explained_var = np.nan if var_y == 0 else 1 - np.var(y_true - y_pred) / var_y

        writer.add_scalar("charts/learning_rate", optimizer.param_groups[0]["lr"], global_step)
        writer.add_scalar("losses/value_loss", v_loss.item(), global_step)
        writer.add_scalar("losses/policy_loss", pg_loss.item(), global_step)
        writer.add_scalar("losses/entropy", entropy_loss.item(), global_step)
        writer.add_scalar("losses/approx_kl", approx_kl.item(), global_step)
        writer.add_scalar("losses/clipfrac", np.mean(clipfracs), global_step)
        writer.add_scalar("losses/explained_variance", explained_var, global_step)
        print("SPS:", int(global_step / (time.time() - start_time)))
        writer.add_scalar("charts/SPS", int(global_step / (time.time() - start_time)), global_step)
        writer.add_scalar("rle/intrinsic_reward_mean", intrinsic_reward.mean().item(), global_step)
        writer.add_scalar("rle/feature_std_mean", feature_network.feat_std.mean().item(), global_step)

    envs.close()
    writer.close()
    record_env.close()