from __future__ import print_function
import datetime
import torch
import numpy as np
import utils.filters as filters
import torch.distributed as dist
import torch.multiprocessing as mp
import os
from binary_snn.utils_binary.training_fl_snn import feedforward_sampling, local_feedback_and_update
from binary_snn.utils_binary.distributed_utils import init_processes, init_training, global_update, global_update_subset
from binary_snn.utils_binary.misc import refractory_period, get_acc_and_loss, save_results
import tables

""""

Runs FL-SNN using two devices. 

"""

def train_fixed_rate(rank, num_nodes, net_params, train_params):
    # Setup training parameters
    # Setup training parameters
    dataset = tables.open_file(train_params['dataset'])
    num_samples_train = train_params['num_samples_train']
    num_samples_test = train_params['num_samples_test']
    test_interval = train_params['test_interval']
    save_path = train_params['save_path']
    labels = train_params['labels']
    num_ite = train_params['ite']
    learning_rate = train_params['learning_rate']
    alpha = train_params['alpha']
    r = train_params['r']
    beta = train_params['beta']
    kappa = train_params['kappa']
    deltas = train_params['deltas']
    rate = train_params['rate']
    tau_list = train_params['tau_list']

    # Create network groups for communication
    all_nodes = dist.new_group([0, 1, 2], timeout=datetime.timedelta(0, 360000))

    S_prime = dataset.root.stats.train_label[-1]
    S = num_samples_train * S_prime

    if rank == 0:
        test_accs = {i: [] for i in tau_list}
        test_indices = np.random.choice(np.arange(num_samples_test), [num_samples_test], replace=False)

        if save_path is None:
            test_acc_save_path = os.getcwd() + r'/results/test_acc_%d_labels_rate_%f.pkl' % (len(labels), rank, rate)
        else:
            test_acc_save_path = save_path + r'/results/test_acc_%d_labels_rate_%f.pkl' % (len(labels), rank, rate)

    for tau in tau_list:
        n_weights_to_send = int(tau * rate)

        for _ in range(num_ite):
            # Initialize main parameters for training
            network, indices_local, weights_list, eligibility_trace, et_temp, learning_signal, ls_temp = init_training(rank, num_nodes, all_nodes, dataset, labels, net_params)
            samples_indices_train = np.random.choice(indices_local, [num_samples_train], replace=True)

            # Gradients accumulator
            gradients_accum = torch.zeros(network.feedforward_weights.shape, dtype=torch.float)
            dist.barrier(all_nodes)

            for s in range(S):
                if rank != 0:
                    if s % S_prime == 0:  # Reset internal state for each example
                        refractory_period(network)

                        sample = torch.cat((torch.FloatTensor(dataset.root.train.data[samples_indices_train[s // S_prime]]),
                                            torch.FloatTensor(dataset.root.train.label[samples_indices_train[s // S_prime]])), dim=0).to(network.device)

                    # lr decay
                    if s % S / 5 == 0:
                        learning_rate /= 2

                    # Feedforward sampling
                    log_proba, ls_temp, et_temp, gradients_accum = feedforward_sampling(network, sample[:, s % S_prime], ls_temp, et_temp, gradients_accum, alpha, r)

                    # Local feedback and update
                    eligibility_trace, et_temp, learning_signal, ls_temp = local_feedback_and_update(network, eligibility_trace, et_temp,
                                                                                                     learning_signal, ls_temp, learning_rate, beta, kappa, s, deltas)

                # Global update
                if (s + 1) % (tau * deltas) == 0:
                    dist.barrier(all_nodes)
                    global_update_subset(all_nodes, rank, network, weights_list, gradients_accum, n_weights_to_send)
                    gradients_accum = torch.zeros(network.feedforward_weights.shape, dtype=torch.float)
                    dist.barrier(all_nodes)

            if rank == 0:
                global_acc, _ = get_acc_and_loss(network, dataset, test_indices)
                test_accs[tau].append(global_acc)
                save_results(test_accs, test_acc_save_path)
                print('Tau: %d, final accuracy: %f' % (tau, global_acc))

    if rank == 0:
        save_results(test_accs, test_acc_save_path)
        print('Training finished and accuracies saved to ' + test_acc_save_path)



def train(rank, num_nodes, net_params, train_params):
    # Setup training parameters
    dataset = tables.open_file(train_params['dataset'])
    num_samples_train = train_params['num_samples_train']
    num_samples_test = train_params['num_samples_test']
    test_interval = train_params['test_interval']
    save_path = train_params['save_path']
    labels = train_params['labels']
    num_ite = train_params['num_ite']
    learning_rate = train_params['learning_rate']
    alpha = train_params['alpha']
    r = train_params['r']
    beta = train_params['beta']
    kappa = train_params['kappa']
    deltas = train_params['deltas']

    # Create network groups for communication
    all_nodes = dist.new_group([0, 1, 2], timeout=datetime.timedelta(0, 360000))

    S_prime = dataset.root.stats.train_data[-1]
    S = num_samples_train * S_prime

    test_loss = {i: [] for i in range(0, S, test_interval)}
    test_loss[S] = []

    test_indices = np.random.choice(np.arange(num_samples_test), [num_samples_test], replace=False)

    if save_path is None:
        test_loss_save_path = os.getcwd() + r'/results/test_loss_%d_labels_node_%d.pkl' % (len(labels), rank)
    else:
        test_loss_save_path = save_path + r'test_loss_%d_labels_node_%d.pkl' % (len(labels), rank)

    for i in range(num_ite):
        # Initialize main parameters for training
        network, indices_local, weights_list, eligibility_trace, et_temp, learning_signal, ls_temp = init_training(rank, num_nodes, all_nodes, dataset, labels, net_params)

        dist.barrier(all_nodes)

        if rank != 0:
            _, loss = get_acc_and_loss(network, dataset, test_indices)
            test_loss[0].append(loss)
            network.set_mode('train')


            samples_indices_train = np.random.choice(indices_local, [num_samples_train], replace=True)

        dist.barrier(all_nodes)


        for s in range(S):
            if rank != 0:
                if s % S_prime == 0:  # Reset internal state for each example
                    refractory_period(network)

                    sample = torch.cat((torch.FloatTensor(dataset.root.train.data[samples_indices_train[s // S_prime]]),
                                        torch.FloatTensor(dataset.root.train.label[samples_indices_train[s // S_prime]])), dim=0).to(network.device)

                # lr decay
                if s % S / 5 == 0:
                    learning_rate /= 2

                # Feedforward sampling
                log_proba, ls_temp, et_temp, _ = feedforward_sampling(network, sample[:, s % S_prime], ls_temp, et_temp, alpha, r)

                # Local feedback and update
                eligibility_trace, et_temp, learning_signal, ls_temp = local_feedback_and_update(network, eligibility_trace, et_temp,
                                                                                                 learning_signal, ls_temp, learning_rate, beta, kappa, s, deltas)

                ## Every few timesteps, record test losses
                if ((s + 1) % test_interval == 0) & ((s + 1) != S):
                    _, loss = get_acc_and_loss(network, dataset, test_indices)
                    test_loss[s + 1].append(loss)
                    save_results(test_loss, test_loss_save_path)
                    network.set_mode('train')

            # Global update
            if (s + 1) % (tau * deltas) == 0:
                dist.barrier(all_nodes)
                global_update(all_nodes, rank, network, weights_list)
                dist.barrier(all_nodes)

        if rank == 0:
            global_acc, _ = get_acc_and_loss(network, dataset, test_indices)
            print('Iteration: %d, final accuracy: %f' % (i, global_acc))
        else:
            _, loss = get_acc_and_loss(network, dataset, test_indices)
            test_loss[S].append(loss)
            save_results(test_loss, test_loss_save_path)

    if rank != 0:
        save_results(test_loss, test_loss_save_path)
        print('Training finished and test loss saved to ' + test_loss_save_path)


if __name__ == "__main__":
    # setting the hyper parameters
    import argparse
    parser = argparse.ArgumentParser(description='Train probabilistic SNNs in a distributed fashion using Pytorch')
    # Mandatory arguments
    parser.add_argument('--dist_url', type=str, help='URL to specify the initialization method of the process group')
    parser.add_argument('--node_rank', type=int, help='Rank of the current node')
    parser.add_argument('--world_size', default=1, type=int, help='Total number of processes to run')
    parser.add_argument('--processes_per_node', default=1, type=int, help='Number of processes in the node')
    parser.add_argument('--dataset', help='Path to the dataset')
    parser.add_argument('--labels', nargs='+', default=None, type=int)

    # Pytorch arguments
    parser.add_argument('--backend', default='gloo', choices=['gloo', 'nccl', 'mpi', 'tcp'], help='Communication backend to use')

    # Training arguments
    parser.add_argument('--num_ite', default=10, type=int, help='Number of times every experiment will be repeated')
    parser.add_argument('--num_samples', default=200, type=int, help='Number of samples to train on for each experiment')
    parser.add_argument('--num_samples_test', default=200, type=int, help='Number of samples to test on')
    parser.add_argument('--test_interval', default=40, type=int, help='Test interval')
    parser.add_argument('--rate', default=None, type=float, help='Fixed communication rate')
    parser.add_argument('--save_path', default=None)

    # SNN arguments
    parser.add_argument('--n_hidden_neurons', default=0, type=int)
    parser.add_argument('--n_basis_ff', default=8, type=int)
    parser.add_argument('--n_basis_fb', default=1, type=int)
    parser.add_argument('--topology_type', default='fully_connected', choices=['fully_connected', 'sparse', 'feedforward'], type=str)
    parser.add_argument('--tau_ff', default=10, type=int, help='Feedforward filter length')
    parser.add_argument('--tau_fb', default=10, type=int, help='Feedback filter length')
    parser.add_argument('--ff_filter', default='raised_cosine_pillow_08', help='Feedforward filter type')
    parser.add_argument('--fb_filter', default='raised_cosine_pillow_08', help='Feedback filter type')
    parser.add_argument('--mu', default=1.5, type=float, help='Filters width')
    parser.add_argument('--lr', default=0.05, type=float, help='Learning rate')
    parser.add_argument('--tau', default=1, type=int, help='Global update period.')
    parser.add_argument('--tau_list', nargs='+', default=None, type=int, help='List of update period.')
    parser.add_argument('--kappa', default=0.2, type=float, help='Learning signal and eligibility trace decay coefficient')
    parser.add_argument('--beta', default=0.05, type=float, help='Baseline decay coefficient')
    parser.add_argument('--deltas', default=1, type=int, help='Local update period')
    parser.add_argument('--alpha', default=1, type=float, help='KL regularization strength')
    parser.add_argument('--r', default=0.3, type=float, help='Desired hidden neurons spiking rate')
    parser.add_argument('--weights_magnitude', default=0.05, type=float)

    args = parser.parse_args()
    print(args)

    node_rank = args.node_rank + args.node_rank*(args.processes_per_node - 1)
    n_processes = args.processes_per_node
    assert (args.world_size % n_processes == 0), 'Each node must have the same number of processes'
    assert (node_rank + n_processes) <= args.world_size, 'There are more processes specified than world_size'

    n_input_neurons = 26**2
    n_output_neurons = 10
    n_hidden_neurons = args.n_hidden_neurons
    n_neurons = n_input_neurons + n_output_neurons + n_hidden_neurons


    filters_dict = {'base_ff_filter': filters.base_feedforward_filter, 'base_fb_filter': filters.base_feedback_filter, 'cosine_basis': filters.cosine_basis,
                    'raised_cosine': filters.raised_cosine, 'raised_cosine_pillow_05': filters.raised_cosine_pillow_05, 'raised_cosine_pillow_08': filters.raised_cosine_pillow_08}


    tau = args.tau
    if args.rate is not None:
        assert args.tau_list is not None, 'rate and tau_list must be specified together'
        tau = None
    if args.tau_list is not None:
        assert args.rate is not None, 'rate and tau_list must be specified together'
        tau = None


    network_parameters = {'n_input_neurons': n_input_neurons,
                          'n_hidden_neurons': n_hidden_neurons,
                          'n_output_neurons': n_output_neurons,
                          'topology_type': args.topology_type,
                          'n_basis_ff': args.n_basis_ff,
                          'ff_filter': filters_dict[args.ff_filter],
                          'n_basis_fb': 1,
                          'fb_filter': filters_dict[args.ff_filter],
                          'tau_ff': args.tau_ff,
                          'tau_fb': args.tau_ff,
                          'mu': args.mu,
                          'weights_magnitude': args.weights_magnitude,
                          'save_path': args.save_path,
                          }

    training_parameters = {'dataset': args.dataset,
                           'tau': tau,
                           'tau_list': args.tau_list,
                           'rate': args.rate,
                           'learning_rate': args.lr,
                           'num_samples_train': args.num_samples,
                           'num_samples_test': args.num_samples_test,
                           'test_interval': args.test_interval,
                           'labels': args.labels,
                           'kappa': args.kappa,
                           'deltas': args.deltas,
                           'alpha': args.alpha,
                           'beta': args.beta,
                           'r': args.r,
                           'num_ite': args.num_ite,
                           'save_path': args.save_path
                           }
    processes = []
    for local_rank in range(n_processes):
        if args.tau_list is not None:
            p = mp.Process(target=init_processes, args=(node_rank + local_rank, args.world_size, args.backend, args.dist_url, network_parameters, training_parameters, train_fixed_rate))
        else:
            p = mp.Process(target=init_processes, args=(node_rank + local_rank, args.world_size, args.backend, args.dist_url, network_parameters, training_parameters, train))
        p.start()
        processes.append(p)

    for p in processes:
        p.join()
