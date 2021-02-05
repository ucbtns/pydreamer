import numpy as np
import gym
import gym_minigrid
import gym_minigrid.envs


class MiniGrid:

    def __init__(self, env_name, max_steps=1000, seed=1337, agent_init_pos=None, agent_init_dir=0):
        entry_point = getattr(gym_minigrid.envs, env_name)
        self._env = entry_point()
        self._env.max_steps = max_steps
        self._env.seed(seed)
        self.max_steps = max_steps
        self.agent_init_pos = agent_init_pos
        self.agent_init_dir = agent_init_dir

        grid = self._env.grid.encode()  # Grid is already generated when env is created

        self._NUM_OBJECTS = len(gym_minigrid.minigrid.OBJECT_TO_IDX)
        self._NUM_COLORS = len(gym_minigrid.minigrid.COLOR_TO_IDX)
        self._NUM_STATES = len(gym_minigrid.minigrid.STATE_TO_IDX)
        self._NUM_CHANNELS = self._NUM_OBJECTS + self._NUM_COLORS + self._NUM_STATES
        spaces = {}
        spaces['image_ids'] = self._env.observation_space['image']
        spaces['image'] = gym.spaces.Box(0, 255, (7, 7, self._NUM_CHANNELS), np.uint8)
        spaces['image_vis'] = gym.spaces.Box(0, self.max_steps, (7, 7), np.uint16)
        spaces['map'] = gym.spaces.Box(0, 255, grid.shape, np.uint8)  # Assume constant grid size
        spaces['map_vis'] = gym.spaces.Box(0, self.max_steps, grid.shape[0:2], np.uint16)
        self.observation_space = gym.spaces.Dict(spaces)
        self.action_space = self._env.action_space

        self._map_last_seen = np.zeros(grid.shape[0:2], dtype=np.uint16)

    @staticmethod
    def has_action_repeat():
        return False

    def params(self):
        # TODO: get rid of params()
        return self.action_space, None, None

    def step(self, action):
        obs, reward, done, info = self._env.step(action)
        return self._observation(obs), reward, done, info

    def reset(self):
        obs = self._env.reset()
        if self.agent_init_pos:
            # Initialize agent in a fixed position, so it can build a map
            self._env.agent_pos = np.array(self.agent_init_pos)
            self._env.agent_dir = self.agent_init_dir
            self._env.grid.set(*self._env.agent_pos, None)  # Remove if something was there
            obs = self._env.gen_obs()
        self._reset_map_last_seen()
        return self._observation(obs)

    def _observation(self, obs_in):
        img = obs_in['image']

        obs = {}
        obs['image_ids'] = img
        obs['image'] = self._one_hot(img)
        obs['image_vis'] = self._obs_last_seen(img)
        obs['map'] = self._map()
        vis_mask = self._global_vis_mask(img)
        obs['map_vis'] = self._update_map_last_seen(vis_mask)

        for k in obs:
            assert obs[k].shape == self.observation_space[k].shape, f"Wrong shape {k}: {obs[k].shape} != {self.observation_space[k].shape}"
        return obs

    def _one_hot(self, img):
        out = np.zeros(self.observation_space['image'].shape, dtype=self.observation_space['image'].dtype)

        # TODO perf: implement as numpy multi-index without loop
        for i in range(img.shape[0]):
            for j in range(img.shape[1]):
                type_ = img[i, j, 0]
                color = img[i, j, 1]
                state = img[i, j, 2]
                out[i, j, type_] = 255
                out[i, j, self._NUM_OBJECTS + color] = 255
                out[i, j, self._NUM_OBJECTS + self._NUM_COLORS + state] = 255

        return out

    def _map(self, with_agent=True):
        out = self._env.grid.encode()
        if with_agent:
            out[self._env.agent_pos[0]][self._env.agent_pos[1]] = np.array([
                gym_minigrid.minigrid.OBJECT_TO_IDX['agent'],
                gym_minigrid.minigrid.COLOR_TO_IDX['red'],
                self._env.agent_dir
            ])
        return out

    def _reset_map_last_seen(self):
        self._map_last_seen *= 0
        self._map_last_seen += self.max_steps

    def _update_map_last_seen(self, map_vis):
        # Update how long ago each map grid was seen. If not seen, then set to max_steps
        self._map_last_seen += 1
        np.clip(self._map_last_seen, 0, self.max_steps, out=self._map_last_seen)
        self._map_last_seen *= (~map_vis)
        return self._map_last_seen.copy()

    def _obs_last_seen(self, img):
        # When the cells in the observation where last seen
        # - 1000 (max_steps) - seen for the first time
        # - 1 - seen in the previous observation
        # - 0 - not visible
        obs_vis_mask = img[:, :, 0] > 0
        x, y, mask = self._obs_global_coords()
        obs_last_seen = self._map_last_seen[x, y]
        obs_last_seen = np.clip(obs_last_seen + 1, 0, self.max_steps)
        obs_last_seen[~mask] = 0
        obs_last_seen[~obs_vis_mask] = 0
        return obs_last_seen

    def _global_vis_mask(self, img):
        # Mark which cells are visible on the global map
        # Reproducing code from env.render()
        obs_vis_mask = img[:, :, 0] > 0
        glb_vis_mask = np.zeros((self._env.width, self._env.height), dtype=np.bool)

        x, y, mask = self._obs_global_coords()
        glb_vis_mask[x[mask], y[mask]] = obs_vis_mask[mask]
        return glb_vis_mask

    def _obs_global_coords(self):
        n = self._env.agent_view_size  # =7
        x = np.zeros((n, n), int)
        y = np.zeros((n, n), int)
        mask = np.zeros((n, n), bool)

        # Transform from local to global coordinates
        # TODO perf: do without loops
        f_vec = self._env.dir_vec
        r_vec = self._env.right_vec
        top_left = self._env.agent_pos + f_vec * (n - 1) - r_vec * (n // 2)
        for vis_j in range(0, n):
            for vis_i in range(0, n):
                abs_i, abs_j = top_left - (f_vec * vis_j) + (r_vec * vis_i)
                mask[vis_i, vis_j] = (abs_i >= 0 and abs_i < self._env.width and abs_j >= 0 and abs_j < self._env.height)
                if mask[vis_i, vis_j]:
                    x[vis_i, vis_j] = abs_i
                    y[vis_i, vis_j] = abs_j

        return x, y, mask

    @staticmethod
    def render_map(map_, tile_size=16):
        map_ = map_.copy()

        # Find and remove special "agent" object
        agent_pos, agent_dir = None, None
        x, y = (map_[:, :, 0] == gym_minigrid.minigrid.OBJECT_TO_IDX['agent']).nonzero()
        if len(x) > 0:
            x, y = x[0], y[0]
            agent_pos = x, y
            agent_dir = map_[x][y][2]
            map_[x][y] = np.array([1, 0, 0])  # EMPTY

        grid, vis_mask = gym_minigrid.minigrid.Grid.decode(map_)
        img = grid.render(tile_size, agent_pos=agent_pos, agent_dir=agent_dir, highlight_mask=~vis_mask)
        return img

    def close(self):
        pass
